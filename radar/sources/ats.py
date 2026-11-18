"""Collecteurs pour les principaux ATS (Workday, Greenhouse, Lever, SmartRecruiters, Ashby, Workable,
Recruitee, Personio, Oracle HCM). Ces endpoints publics alimentent les pages carrières des entreprises :
une offre y apparaît en général quelques minutes après sa publication."""
from __future__ import annotations

import html as htmllib
from datetime import datetime, timezone
from defusedxml import ElementTree as ET

from ..http import Http
from ..models import Job
from ..textutil import html_to_text
from .base import Source, register


def _dt(s) -> datetime | None:
    if not s:
        return None
    try:
        if isinstance(s, (int, float)):
            return datetime.fromtimestamp(s / 1000 if s > 1e11 else s, tz=timezone.utc)
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None


# ── Greenhouse ────────────────────────────────────────────────────────────────────────────────
@register
class Greenhouse(Source):
    """cfg: board (jeton de la page jobs, ex. 'ardian')."""
    type = "greenhouse"

    async def fetch(self, http: Http) -> list[Job]:
        board = self.cfg["board"]
        data = await http.get_json(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs", params={"content": "true"})
        out = []
        for j in data.get("jobs", []):
            depts = ", ".join(d.get("name", "") for d in j.get("departments", []) if d)
            out.append(self.job(
                j["id"], j.get("title"), j.get("absolute_url", ""),
                location=(j.get("location") or {}).get("name", ""),
                description=html_to_text(htmllib.unescape(j.get("content") or "")),
                department=depts, posted_at=_dt(j.get("first_published") or j.get("updated_at")),
            ))
        return out


# ── Lever ─────────────────────────────────────────────────────────────────────────────────────
@register
class Lever(Source):
    """cfg: site (ex. 'spotify'), region: 'eu' pour api.eu.lever.co."""
    type = "lever"

    async def fetch(self, http: Http) -> list[Job]:
        host = "api.eu.lever.co" if self.cfg.get("region") == "eu" else "api.lever.co"
        data = await http.get_json(f"https://{host}/v0/postings/{self.cfg['site']}", params={"mode": "json"})
        out = []
        for j in data:
            cat = j.get("categories") or {}
            parts = [j.get("descriptionPlain") or html_to_text(j.get("description"))]
            for lst in j.get("lists") or []:
                parts.append(f"{lst.get('text', '')}\n{html_to_text(lst.get('content'))}")
            parts.append(j.get("additionalPlain") or html_to_text(j.get("additional")))
            locs = cat.get("allLocations") or [cat.get("location", "")]
            out.append(self.job(
                j["id"], j.get("text"), j.get("hostedUrl", ""),
                location="; ".join(l for l in locs if l), description="\n".join(p for p in parts if p),
                contract=cat.get("commitment", ""), department=cat.get("team") or cat.get("department") or "",
                posted_at=_dt(j.get("createdAt")),
            ))
        return out


# ── SmartRecruiters ───────────────────────────────────────────────────────────────────────────
@register
class SmartRecruiters(Source):
    """cfg: company (identifiant SmartRecruiters), country (ex. 'fr', optionnel)."""
    type = "smartrecruiters"

    async def fetch(self, http: Http) -> list[Job]:
        cid = self.cfg["company"]
        seen: dict[str, Job] = {}
        for term in self.terms:
            offset = 0
            while True:
                params = {"limit": 100, "offset": offset, "q": term}
                if self.cfg.get("country"):
                    params["country"] = self.cfg["country"]
                data = await http.get_json(f"https://api.smartrecruiters.com/v1/companies/{cid}/postings", params=params)
                content = data.get("content", [])
                for p in content:
                    loc = p.get("location") or {}
                    city = ", ".join(x for x in (loc.get("city"), (loc.get("country") or "").upper()) if x)
                    seen.setdefault(p["id"], self.job(
                        p["id"], p.get("name"), f"https://jobs.smartrecruiters.com/{cid}/{p['id']}",
                        location=city, contract=(p.get("typeOfEmployment") or {}).get("label", ""),
                        department=(p.get("department") or {}).get("label", ""),
                        posted_at=_dt(p.get("releasedDate")), needs_detail=True,
                        extra={"api_id": p["id"], "cid": cid},
                    ))
                offset += len(content)
                if not content or offset >= data.get("totalFound", 0) or offset >= 300:
                    break
        return list(seen.values())

    async def enrich(self, http: Http, job: Job) -> Job:
        d = await http.get_json(f"https://api.smartrecruiters.com/v1/companies/{job.extra['cid']}/postings/{job.extra['api_id']}")
        sec = (d.get("jobAd") or {}).get("sections") or {}
        job.description = "\n".join(
            html_to_text((sec.get(k) or {}).get("text")) for k in
            ("jobDescription", "qualifications", "additionalInformation") if sec.get(k)
        )
        job.url = d.get("postingUrl") or job.url
        job.needs_detail = False
        return job


# ── Workday ───────────────────────────────────────────────────────────────────────────────────
@register
class Workday(Source):
    """cfg: host ('lazard.wd5.myworkdayjobs.com'), site ('Lazard_Careers'), tenant (optionnel)."""
    type = "workday"
    PAGE = 20

    def _base(self) -> str:
        host = self.cfg["host"]
        tenant = self.cfg.get("tenant") or host.split(".")[0]
        return f"https://{host}/wday/cxs/{tenant}/{self.cfg['site']}"

    async def fetch(self, http: Http) -> list[Job]:
        base = self._base()
        host, site = self.cfg["host"], self.cfg["site"]
        cap = int(self.cfg.get("max_results", 200))
        found: dict[str, Job] = {}
        for term in self.terms:
            offset = 0
            while offset < cap:
                body = {"appliedFacets": self.cfg.get("facets", {}), "limit": self.PAGE, "offset": offset, "searchText": term}
                data = await http.post_json(f"{base}/jobs", json=body)
                posts = data.get("jobPostings", [])
                for p in posts:
                    path = p.get("externalPath")
                    if not path:
                        continue
                    ids = p.get("bulletFields") or []
                    found.setdefault(path, self.job(
                        ids[0] if ids else path, p.get("title"), f"https://{host}/{site}{path}",
                        location=p.get("locationsText", ""), needs_detail=True, extra={"path": path},
                    ))
                offset += self.PAGE
                if not posts or offset >= data.get("total", 0):
                    break
        return list(found.values())

    async def enrich(self, http: Http, job: Job) -> Job:
        d = await http.get_json(f"{self._base()}{job.extra['path']}")
        info = d.get("jobPostingInfo") or {}
        job.description = html_to_text(info.get("jobDescription"))
        locs = [info.get("location", "")] + list(info.get("additionalLocations") or [])
        loc = "; ".join(l for l in locs if l)
        if loc:
            job.location = loc
        job.start_hint = info.get("startDate") or ""
        job.url = info.get("externalUrl") or job.url
        job.contract = job.contract or info.get("timeType", "")
        job.needs_detail = False
        return job


# ── Ashby ─────────────────────────────────────────────────────────────────────────────────────
@register
class Ashby(Source):
    """cfg: board (ex. 'acme')."""
    type = "ashby"

    async def fetch(self, http: Http) -> list[Job]:
        data = await http.get_json(f"https://api.ashbyhq.com/posting-api/job-board/{self.cfg['board']}")
        out = []
        for j in data.get("jobs", []):
            if j.get("isListed") is False:
                continue
            out.append(self.job(
                j["id"], j.get("title"), j.get("jobUrl") or j.get("applyUrl", ""),
                location=j.get("location", ""),
                description=j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml")),
                contract=j.get("employmentType", ""), department=j.get("department") or j.get("team") or "",
                posted_at=_dt(j.get("publishedAt")),
            ))
        return out


# ── Workable ──────────────────────────────────────────────────────────────────────────────────
@register
class Workable(Source):
    """cfg: account (ex. 'acme')."""
    type = "workable"

    async def fetch(self, http: Http) -> list[Job]:
        acct = self.cfg["account"]
        out, token = [], None
        for _ in range(6):
            body = {"query": "", "location": [], "department": [], "worktype": [], "remote": []}
            if token:
                body["token"] = token
            data = await http.post_json(f"https://apply.workable.com/api/v3/accounts/{acct}/jobs", json=body)
            for j in data.get("results", []):
                loc = j.get("location") or {}
                city = ", ".join(x for x in (loc.get("city"), loc.get("country")) if x)
                out.append(self.job(
                    j["shortcode"], j.get("title"), f"https://apply.workable.com/{acct}/j/{j['shortcode']}/",
                    location=city, contract=j.get("employment_type", "") or j.get("type", ""),
                    department=", ".join(j.get("department") or []) if isinstance(j.get("department"), list) else (j.get("department") or ""),
                    posted_at=_dt(j.get("published")), needs_detail=True, extra={"acct": acct, "sc": j["shortcode"]},
                ))
            token = data.get("nextPage")
            if not token:
                break
        return out

    async def enrich(self, http: Http, job: Job) -> Job:
        d = await http.get_json(f"https://apply.workable.com/api/v2/accounts/{job.extra['acct']}/jobs/{job.extra['sc']}")
        job.description = "\n".join(html_to_text(d.get(k)) for k in ("description", "requirements", "benefits") if d.get(k))
        job.needs_detail = False
        return job


# ── Recruitee ─────────────────────────────────────────────────────────────────────────────────
@register
class Recruitee(Source):
    """cfg: company (sous-domaine, ex. 'acme' pour acme.recruitee.com)."""
    type = "recruitee"

    async def fetch(self, http: Http) -> list[Job]:
        data = await http.get_json(f"https://{self.cfg['company']}.recruitee.com/api/offers/")
        out = []
        for j in data.get("offers", []):
            out.append(self.job(
                j["id"], j.get("title"), j.get("careers_url", ""),
                location=", ".join(x for x in (j.get("city"), j.get("country")) if x) or j.get("location", ""),
                description=html_to_text((j.get("description") or "") + "\n" + (j.get("requirements") or "")),
                contract=j.get("employment_type_code", ""), department=j.get("department") or "",
                posted_at=_dt(j.get("created_at")),
            ))
        return out


# ── Personio ──────────────────────────────────────────────────────────────────────────────────
@register
class Personio(Source):
    """cfg: company (sous-domaine), tld: 'de' (défaut) ou 'com'."""
    type = "personio"

    async def fetch(self, http: Http) -> list[Job]:
        tld = self.cfg.get("tld", "de")
        base = f"https://{self.cfg['company']}.jobs.personio.{tld}"
        xml = await http.get_text(f"{base}/xml", params={"language": self.cfg.get("language", "en")})
        root = ET.fromstring(xml)
        out = []
        for p in root.iter("position"):
            def g(tag, default=""):
                el = p.find(tag)
                return (el.text or default).strip() if el is not None and el.text else default
            descs = []
            for jd in p.iter("jobDescription"):
                n, v = jd.find("name"), jd.find("value")
                descs.append(f"{(n.text or '') if n is not None else ''}\n{html_to_text(v.text if v is not None else '')}")
            out.append(self.job(
                g("id"), g("name"), f"{base}/job/{g('id')}",
                location=g("office"), description="\n".join(descs), contract=g("employmentType"),
                department=g("department"), posted_at=_dt(g("createdAt")),
            ))
        return out


# ── Oracle HCM (Candidate Experience) — ex. JPMorgan ─────────────────────────────────────────
@register
class OracleHCM(Source):
    """cfg: host ('jpmc.fa.oraclecloud.com'), site ('CX_1001'), lang ('en')."""
    type = "oracle_hcm"
    PAGE = 25

    def _api(self) -> str:
        return f"https://{self.cfg['host']}/hcmRestApi/resources/latest"

    async def fetch(self, http: Http) -> list[Job]:
        host, site, lang = self.cfg["host"], self.cfg["site"], self.cfg.get("lang", "en")
        cap = int(self.cfg.get("max_results", 150))
        found: dict[str, Job] = {}
        for term in self.terms:
            offset = 0
            while offset < cap:
                finder = (f"findReqs;siteNumber={site},limit={self.PAGE},offset={offset},"
                          f'keyword="{term}",sortBy=POSTING_DATES_DESC')
                data = await http.get_json(
                    f"{self._api()}/recruitingCEJobRequisitions",
                    params={"onlyData": "true", "expand": "requisitionList.secondaryLocations", "finder": finder},
                )
                items = data.get("items") or [{}]
                reqs = items[0].get("requisitionList", [])
                for r in reqs:
                    rid = str(r["Id"])
                    locs = [r.get("PrimaryLocation", "")] + [s.get("Name", "") for s in r.get("secondaryLocations", [])]
                    found.setdefault(rid, self.job(
                        rid, r.get("Title"), f"https://{host}/hcmUI/CandidateExperience/{lang}/sites/{site}/job/{rid}",
                        location="; ".join(l for l in locs if l), description=r.get("ShortDescriptionStr") or "",
                        posted_at=_dt(r.get("PostedDate")), needs_detail=True, extra={"rid": rid},
                    ))
                offset += self.PAGE
                if not reqs or offset >= int(items[0].get("TotalJobsCount", 0) or 0):
                    break
        return list(found.values())

    async def enrich(self, http: Http, job: Job) -> Job:
        site = self.cfg["site"]
        d = await http.get_json(
            f"{self._api()}/recruitingCEJobRequisitionDetails",
            params={"expand": "all", "onlyData": "true", "finder": f'ById;Id="{job.extra["rid"]}",siteNumber={site}'},
        )
        it = (d.get("items") or [{}])[0]
        job.description = "\n".join(
            html_to_text(it.get(k)) for k in ("ExternalDescriptionStr", "ExternalResponsibilitiesStr", "ExternalQualificationsStr") if it.get(k)
        ) or job.description
        job.needs_detail = False
        return job
