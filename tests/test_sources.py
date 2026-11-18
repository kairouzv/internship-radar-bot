import asyncio
import json
from email.message import EmailMessage

import httpx
import pytest

from radar.http import Http
from radar.sources import REGISTRY
from radar.sources.discover import detect
from radar.sources.email_alerts import parse_alert_email
from radar.sources.feeds import parse_jobposting_jsonld


def run(coro):
    return asyncio.run(coro)


def make_http(handler):
    return Http(httpx.AsyncClient(transport=httpx.MockTransport(handler)), retries=0)


def mk(type_, **cfg):
    cfg.setdefault("name", "Test")
    return REGISTRY[type_](dict(type=type_, **cfg), lambda n: {"FRANCETRAVAIL_CLIENT_ID": "id", "FRANCETRAVAIL_CLIENT_SECRET": "s",
                                                               "ADZUNA_APP_ID": "a", "ADZUNA_APP_KEY": "k"}.get(n, ""))


def test_greenhouse():
    def h(req):
        assert "boards-api.greenhouse.io/v1/boards/acme/jobs" in str(req.url)
        return httpx.Response(200, json={"jobs": [{
            "id": 11, "title": "Stage M&A", "absolute_url": "https://boards.greenhouse.io/acme/jobs/11",
            "location": {"name": "Paris, France"}, "departments": [{"name": "Investment Banking"}],
            "content": "&lt;p&gt;Start date: June 2027. Duration: 6 months.&lt;/p&gt;", "updated_at": "2026-11-01T10:00:00Z"}]})
    jobs = run(mk("greenhouse", board="acme").fetch(make_http(h)))
    assert len(jobs) == 1 and jobs[0].location == "Paris, France"
    assert "Start date: June 2027" in jobs[0].description and jobs[0].department == "Investment Banking"


def test_lever():
    def h(req):
        return httpx.Response(200, json=[{
            "id": "abc", "text": "Intern Leveraged Finance", "hostedUrl": "https://jobs.lever.co/acme/abc",
            "categories": {"location": "Paris", "commitment": "Intern", "team": "LevFin"},
            "descriptionPlain": "Join us. Starting in May 2027.", "lists": [{"text": "Req", "content": "<li>Excel</li>"}],
            "additionalPlain": "", "createdAt": 1760000000000}])
    j = run(mk("lever", site="acme").fetch(make_http(h)))[0]
    assert j.contract == "Intern" and "May 2027" in j.description and j.location == "Paris"


def test_smartrecruiters_list_and_detail():
    def h(req):
        if req.url.path.endswith("/postings") and "offset" in req.url.params:
            if req.url.params["offset"] != "0":
                return httpx.Response(200, json={"content": [], "totalFound": 1})
            return httpx.Response(200, json={"totalFound": 1, "content": [{
                "id": "999", "name": "Stage Corporate Finance", "location": {"city": "Paris", "country": "fr"},
                "typeOfEmployment": {"label": "Internship"}, "department": {"label": "Finance"}, "releasedDate": "2026-11-10T00:00:00Z"}]})
        return httpx.Response(200, json={"postingUrl": "https://jobs.smartrecruiters.com/acme/999-stage",
                                         "jobAd": {"sections": {"jobDescription": {"text": "<p>Start date: July 2027</p>"}}}})
    src = mk("smartrecruiters", company="acme", search=["stage"])
    http = make_http(h)
    jobs = run(src.fetch(http))
    assert jobs[0].needs_detail and jobs[0].contract == "Internship"
    j = run(src.enrich(http, jobs[0]))
    assert "July 2027" in j.description and not j.needs_detail and j.url.endswith("999-stage")


def test_workday_pagination_and_detail():
    calls = []

    def h(req):
        if req.method == "POST":
            body = json.loads(req.content)
            calls.append(body["offset"])
            posts = [{"title": f"Stage {i}", "externalPath": f"/job/Paris/Stage-{i}_R{i}", "locationsText": "Paris",
                      "bulletFields": [f"R{i}"]} for i in range(body["offset"], min(body["offset"] + 20, 25))]
            return httpx.Response(200, json={"total": 25, "jobPostings": posts})
        return httpx.Response(200, json={"jobPostingInfo": {
            "jobDescription": "<p>Starting in June 2027 for 6 months</p>", "location": "Paris, France",
            "startDate": "2027-06-01", "externalUrl": "https://lazard.wd5.myworkdayjobs.com/Careers/job/x"}})
    src = mk("workday", host="lazard.wd5.myworkdayjobs.com", site="Careers", search=["stage"])
    http = make_http(h)
    jobs = run(src.fetch(http))
    assert len(jobs) == 25 and calls == [0, 20]
    j = run(src.enrich(http, jobs[0]))
    assert j.start_hint == "2027-06-01" and "6 months" in j.description and j.location == "Paris, France"


def test_ashby_workable_recruitee():
    def h(req):
        u = str(req.url)
        if "ashbyhq" in u:
            return httpx.Response(200, json={"jobs": [{"id": "1", "title": "Intern PE", "location": "Paris", "employmentType": "Intern",
                                                       "jobUrl": "https://jobs.ashbyhq.com/a/1", "descriptionPlain": "x", "isListed": True},
                                                      {"id": "2", "title": "Hidden", "isListed": False}]})
        if "apply.workable.com/api/v3" in u:
            return httpx.Response(200, json={"results": [{"shortcode": "AB12", "title": "Stage PE", "location": {"city": "Paris", "country": "France"}}]})
        if "apply.workable.com/api/v2" in u:
            return httpx.Response(200, json={"description": "<p>Start: May 2027</p>"})
        if "recruitee" in u:
            return httpx.Response(200, json={"offers": [{"id": 5, "title": "Stage Analyste", "careers_url": "https://a.recruitee.com/o/5",
                                                         "city": "Paris", "country": "France", "description": "<p>x</p>"}]})
        return httpx.Response(404)
    http = make_http(h)
    assert [j.title for j in run(mk("ashby", board="a").fetch(http))] == ["Intern PE"]
    wj = run(mk("workable", account="a").fetch(http))[0]
    assert wj.location == "Paris, France"
    assert "May 2027" in run(mk("workable", account="a").enrich(http, wj)).description
    assert run(mk("recruitee", company="a").fetch(http))[0].location == "Paris, France"


def test_personio_xml():
    xml = """<?xml version="1.0"?><workzag-jobs><position><id>77</id><office>Paris</office><department>Finance</department>
    <name>Praktikum M&amp;A</name><jobDescriptions><jobDescription><name>Role</name><value>&lt;p&gt;Beginn: Mai 2027&lt;/p&gt;</value></jobDescription></jobDescriptions>
    <employmentType>intern</employmentType><createdAt>2026-11-01T10:00:00+00:00</createdAt></position></workzag-jobs>"""
    j = run(mk("personio", company="a").fetch(make_http(lambda r: httpx.Response(200, text=xml))))[0]
    assert j.title == "Praktikum M&A" and j.contract == "intern" and "Mai 2027" in j.description and j.url.endswith("/job/77")


def test_oracle_hcm():
    def h(req):
        if "recruitingCEJobRequisitionDetails" in str(req.url):
            return httpx.Response(200, json={"items": [{"ExternalDescriptionStr": "<p>Start date: June 2027, 6 months</p>"}]})
        return httpx.Response(200, json={"items": [{"TotalJobsCount": 1, "requisitionList": [{
            "Id": 123, "Title": "Summer Analyst - M&A", "PrimaryLocation": "Paris, France", "PostedDate": "2026-11-05",
            "secondaryLocations": [{"Name": "London"}]}]}]})
    src = mk("oracle_hcm", host="jpmc.fa.oraclecloud.com", site="CX_1001", search=["intern"])
    http = make_http(h)
    j = run(src.fetch(http))[0]
    assert "Paris" in j.location and j.url.endswith("/sites/CX_1001/job/123")
    assert "June 2027" in run(src.enrich(http, j)).description


RSS = """<?xml version="1.0"?><rss xmlns:tt="https://teamtailor.com/locations" version="2.0"><channel>
<item><guid>g1</guid><title>Stage Private Equity</title><link>https://x.teamtailor.com/jobs/1</link>
<description>&lt;p&gt;Start in July 2027&lt;/p&gt;</description><tt:locations><tt:location><tt:city>Paris</tt:city></tt:location></tt:locations></item>
</channel></rss>"""


def test_rss():
    j = run(mk("rss", url="https://x/jobs.rss").fetch(make_http(lambda r: httpx.Response(200, text=RSS))))[0]
    assert j.title == "Stage Private Equity" and j.location == "Paris" and "July 2027" in j.description


PAGE = """<html><body><a class="job" href="/jobs/42-stage-m-a">Stage M&A<br>Paris</a><a href="/about">About</a>
<script type="application/ld+json">{"@context":"https://schema.org","@type":"JobPosting","title":"Stage LevFin","description":"<p>Start date: June 2027</p>",
"identifier":{"value":"LF1"},"url":"https://c.com/jobs/lf1","jobLocation":{"address":{"addressLocality":"Paris","addressCountry":"FR"}},
"employmentType":"INTERN","hiringOrganization":{"name":"C"}}</script></body></html>"""


def test_html_and_jsonld():
    src = mk("html", url="https://c.com/careers", link_selector="a.job", include_regex="/jobs/")
    jobs = run(src.fetch(make_http(lambda r: httpx.Response(200, text=PAGE))))
    titles = sorted(j.title for j in jobs)
    assert titles == ["Stage LevFin", "Stage M&A"]
    ld = [j for j in jobs if j.title == "Stage LevFin"][0]
    assert ld.location == "Paris, FR" and ld.contract == "INTERN"
    assert len(parse_jobposting_jsonld(PAGE)) == 1


def test_html_enrich_from_page():
    src = mk("html", url="https://c.com/careers", link_selector="a.job")
    jobs = run(src.fetch(make_http(lambda r: httpx.Response(200, text=PAGE))))
    j = [x for x in jobs if x.title == "Stage M&A"][0]
    assert j.needs_detail and j.location == "Paris"
    j = run(src.enrich(make_http(lambda r: httpx.Response(200, text="<main><h1>Stage M&A</h1><p>Démarrage : mai 2027</p></main>")), j))
    assert "mai 2027" in j.description and not j.needs_detail


def test_sitemap():
    idx = '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><sitemap><loc>https://c.com/s1.xml</loc></sitemap></sitemapindex>'
    s1 = '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://c.com/jobs/stage-m-a-paris-12345</loc></url><url><loc>https://c.com/about</loc></url></urlset>'

    def h(req):
        return httpx.Response(200, text=idx if req.url.path.endswith("sitemap.xml") else s1)
    jobs = run(mk("sitemap", url="https://c.com/sitemap.xml", url_regex="/jobs/").fetch(make_http(h)))
    assert len(jobs) == 1 and jobs[0].title.lower().startswith("stage m a paris") and jobs[0].needs_detail


def test_francetravail_and_adzuna():
    def h(req):
        u = str(req.url)
        if "access_token" in u:
            return httpx.Response(200, json={"access_token": "tok"})
        if "francetravail.io" in u:
            if req.url.params["departement"] == "92":
                return httpx.Response(204)
            return httpx.Response(206, json={"resultats": [{"id": "X1", "intitule": "Stage Analyste Financier",
                                                            "lieuTravail": {"libelle": "75 - PARIS 08"}, "entreprise": {"nom": "ACME"},
                                                            "description": "Début : juin 2027", "typeContratLibelle": "Stage"}]})
        if "adzuna" in u:
            return httpx.Response(200, json={"results": [{"id": 9, "title": "Stage M&A", "company": {"display_name": "Bank"},
                                                          "location": {"display_name": "Paris, Île-de-France"}, "description": "snippet",
                                                          "redirect_url": "https://adzuna.fr/x", "contract_type": "internship"}]})
        return httpx.Response(404)
    http = make_http(h)
    ft = run(mk("francetravail", search=["stage finance"]).fetch(http))
    assert len(ft) == 1 and ft[0].company == "ACME" and "PARIS" in ft[0].location
    az = run(mk("adzuna", search=["stage m&a"], where="Paris").fetch(http))
    assert az[0].url == "https://adzuna.fr/x"
    # secrets absents -> source ignorée
    assert REGISTRY["adzuna"]({"name": "a", "type": "adzuna"}).missing()


def _email(html_body: str) -> bytes:
    m = EmailMessage()
    m["From"] = "LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>"
    m["Subject"] = "Stage finance : 3 nouvelles offres"
    m.set_content("plain")
    m.add_alternative(html_body, subtype="html")
    return m.as_bytes()


def test_parse_linkedin_like_alert():
    body = """<html><body><table>
    <tr><td><a href="https://www.linkedin.com/comm/jobs/view/4123456789?trackingId=abc&refId=x">Stage M&amp;A (H/F)</a>
        <div>Lazard</div><div>Paris, Île-de-France, France</div>
        <a href="https://www.linkedin.com/comm/jobs/view/4123456789?trackingId=abc">Voir l'offre</a></td></tr>
    <tr><td><a href="https://www.linkedin.com/comm/jobs/view/4999999999?x=1"><span>Stagiaire Leveraged Finance</span></a>
        <div>Société Générale · Paris</div></td></tr>
    <tr><td><a href="https://www.linkedin.com/comm/jobs/search?keywords=stage">Voir plus d'offres</a></td></tr>
    </table></body></html>"""
    out = parse_alert_email(_email(body))
    assert len(out) == 2
    a = [o for o in out if o["id"] == "li-4123456789"][0]
    assert a["url"] == "https://www.linkedin.com/jobs/view/4123456789" and a["title"] == "Stage M&A (H/F)"
    assert a["company"] == "Lazard" and "Paris" in a["location"]
    b = [o for o in out if o["id"] == "li-4999999999"][0]
    assert b["title"] == "Stagiaire Leveraged Finance" and b["company"] == "Société Générale" and b["location"] == "Paris"


def test_parse_wttj_and_jobs_ch():
    body = """<html><body>
    <a href="https://www.welcometothejungle.com/fr/companies/rothschild-co/jobs/stage-ma_paris?q=1">Stage M&A</a>
    <a href="https://www.jobs.ch/en/vacancies/detail/0a1b2c3d-1111-2222-3333-444455556666/?source=alert">Praktikant Corporate Finance</a>
    </body></html>"""
    out = parse_alert_email(_email(body))
    ids = {o["id"] for o in out}
    assert "wttj-rothschild-co-stage-ma_paris" in ids and any(i.startswith("jobsch-") for i in ids)


def test_discover_detect():
    html = '<a href="https://lazard.wd5.myworkdayjobs.com/en-US/Lazard_Careers/job/Paris/x_R1">Apply</a>' \
           '<script>var u="https:\\/\\/boards.greenhouse.io\\/ardian"</script><a href="https://jobs.lever.co/foo">x</a>'
    res = detect("https://x.com/careers", html, "X")
    types = {s["type"] for s in res["suggestions"]}
    assert {"workday", "greenhouse", "lever"} <= types
    wd = [s for s in res["suggestions"] if s["type"] == "workday"][0]
    assert wd["host"] == "lazard.wd5.myworkdayjobs.com" and wd["site"] == "Lazard_Careers"
    res = detect("https://y.com", '<a href="https://y.taleo.net/careersection/x">x</a>', "Y")
    assert not res["suggestions"] and "Taleo" in res["notes"][0]


def test_auto_source_detects_and_caches():
    from radar.store import Store
    hits = {"page": 0, "api": 0}

    def h(req):
        u = str(req.url)
        if "careers.acme.com" in u:
            hits["page"] += 1
            return httpx.Response(200, text='<a href="https://boards.greenhouse.io/acme">Jobs</a>')
        if "boards-api.greenhouse.io" in u:
            hits["api"] += 1
            return httpx.Response(200, json={"jobs": [{"id": 1, "title": "Stage M&A", "absolute_url": "https://gh/1",
                                                       "location": {"name": "Paris"}, "content": "x", "departments": []}]})
        return httpx.Response(404)
    http = make_http(h)
    store = Store(":memory:")
    src = mk("auto", name="Acme", url="https://careers.acme.com/")
    src.bind(store)
    jobs = run(src.fetch(http))
    assert jobs[0].source == "auto:Acme" and jobs[0].extra["via"] == "greenhouse:Acme"
    run(src.fetch(http))
    assert hits == {"page": 1, "api": 2}          # détection mémorisée : la page carrières n'est relue qu'une fois


def test_auto_source_unrecognized_page_error_is_explicit():
    src = mk("auto", name="Opaque", url="https://x.com/careers")
    with pytest.raises(Exception) as e:
        run(src.fetch(make_http(lambda r: httpx.Response(200, text="<html>JS only</html>"))))
    assert "Aucun ATS reconnu" in str(e.value) or "aucun ATS" in str(e.value).lower()
