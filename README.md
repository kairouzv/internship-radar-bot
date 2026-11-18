# 📡 Internship Radar

Bot **gratuit** qui surveille les offres de **stage en finance (Paris, Genève)** et t'envoie sur **Telegram et/ou Discord**, dès qu'elles apparaissent, uniquement celles qui correspondent à ton profil **et à tes dates de stage**.

```
🔥 Stage M&A (H/F)
Lazard · 📍 Paris, France
🗓 Début : juin 2027 ✅
⏱ Durée : 6 mois ✅
🎯 M&A · score 100/100
🔎 workday:Lazard
[🔗 Voir l'offre]
```

## Ce que fait le bot

| Étape | Détail |
|---|---|
| **Collecte** (toutes les ~10 min) | Pages carrières des banques / fonds (Workday, Greenhouse, Lever, SmartRecruiters, Ashby, Workable, Recruitee, Personio, Oracle HCM, RSS, sitemaps, pages HTML) + **tes alertes e-mail** LinkedIn / Indeed / Welcome to the Jungle / eFinancialCareers / jobs.ch + APIs gratuites (Adzuna, France Travail). |
| **Filtre « stage »** | Écarte CDI, alternance, VIE, programmes graduate (réglable). |
| **Filtre lieu** | Paris / Île-de-France / La Défense, Genève (réglable). |
| **Filtre métier** | Score 0-100 : M&A, Leveraged Finance, Coverage/IB, PE/dette privée, Capital Markets, Restructuring… (réglable). |
| **Filtre dates** ⭐ | Lit la **date de début** et la **durée** dans le texte de l'annonce (FR/EN/DE : « starting June 2027 », « stage de 6 mois à partir de mai », « Q2 2027 », « du 3 mai au 31 octobre »…) et les compare à ta fenêtre. |
| **Dédoublonnage** | Une offre vue via LinkedIn *et* le site de la banque = une seule alerte. |
| **Alertes** | 🔥 TOP (dates OK + métier parfait) · ✅ BON MATCH · ❓ DATES À VÉRIFIER (annonce sans date — très fréquent). Les offres pertinentes mais aux mauvaises dates vont dans le **résumé du matin**, pas en alerte. |
| **Résumé quotidien** | Alertes de la veille, « presque bonnes » offres (avec la raison), état des sources. |
| **Surveillance** | Si une source qui marchait tombe en panne ou renvoie 0 offre, tu es prévenu (et quand elle revient). |

Premier lancement : **un seul message récapitulatif** des offres déjà ouvertes, pas de rafale.

---

## Mise en route (≈ 20 minutes)

### 1. Créer les canaux de notification

**Telegram** (recommandé : notifications push instantanées avec bouton « Voir l'offre »)
1. Dans Telegram, ouvre **@BotFather** → `/newbot` → note le **token** (`123456:ABC…`).
2. Envoie `/start` à ton nouveau bot.
3. Récupère ton identifiant : `TELEGRAM_BOT_TOKEN=<token> python -m radar telegram-chat-id` → copie `TELEGRAM_CHAT_ID`.

**Discord** (optionnel, en plus ou à la place)
- Salon → ⚙️ **Intégrations → Webhooks → Nouveau webhook** → copie l'URL → `DISCORD_WEBHOOK_URL`.

### 2. Mettre le projet sur GitHub

1. Crée un dépôt GitHub **public** (minutes Actions illimitées et gratuites ; vois la note « dépôt privé » plus bas) et pousse ce dossier :
   ```bash
   git init && git add . && git commit -m "radar" && git branch -M main
   git remote add origin https://github.com/<toi>/internship-radar.git && git push -u origin main
   ```
2. **Settings → Secrets and variables → Actions → New repository secret** : ajoute
   `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (et/ou `DISCORD_WEBHOOK_URL`).
   *Les secrets ne sont jamais visibles, même si le dépôt est public.*
3. Onglet **Actions** → active les workflows si GitHub le demande.

### 3. Tester

1. **Actions → doctor → Run workflow** : teste *chaque* source depuis les serveurs GitHub et affiche, pour chacune, le nombre d'offres / de stages / d'offres pertinentes. C'est l'étape la plus importante : elle te dit ce qui marche vraiment. (Les entreprises dont la page carrières n'est pas reconnue apparaissent en `FAIL` : voir « Ajouter une entreprise ».)
2. En local (optionnel) : `pip install -r requirements.txt && python -m radar test-notify` envoie un message de test.
3. **Actions → radar → Run workflow** : premier passage. Tu reçois « 🚀 Radar activé » avec les meilleures offres déjà ouvertes. Ensuite, le bot tourne tout seul toutes les ~10 minutes.

### 4. Ajouter les alertes e-mail (la plus grosse source de couverture)

LinkedIn, Indeed, WTTJ… n'ont pas d'API publique et interdisent le scraping. Solution propre : **ils t'envoient eux-mêmes leurs offres**, le bot les lit.

1. Sur chaque site, crée des alertes **à fréquence maximale** (« immédiate » / « quotidienne » si pas mieux) :
   - LinkedIn Jobs : recherches `stage M&A`, `internship leveraged finance`, `stage private equity`, `stage corporate finance`… — lieu Paris / Genève — filtre **Stage**.
   - Welcome to the Jungle, eFinancialCareers (très pertinent en banque), jobs.ch / jobup.ch (Genève), Indeed, APEC, HelloWork.
2. Dans Gmail : **Paramètres → Filtres** → expéditeurs `linkedin.com`, `indeed.com`, `welcometothejungle.com`, `efinancialcareers`, `jobs.ch`… → *Appliquer le libellé* **JobAlerts** (+ « Ne pas envoyer dans la boîte de réception » si tu veux).
3. Active l'IMAP (Gmail → Paramètres → Transfert et POP/IMAP), active la validation en 2 étapes, puis crée un **mot de passe d'application** (compte Google → Sécurité → Mots de passe des applications).
4. Ajoute les secrets `IMAP_USER` (ton adresse Gmail) et `IMAP_PASSWORD` (le mot de passe d'application). Le bot lit le libellé en **lecture seule**.

> Les e-mails ne contiennent presque jamais les dates. Pour Welcome to the Jungle, jobs.ch, eFinancialCareers, APEC et HelloWork, le bot ouvre la fiche publique pour les lire. Pour LinkedIn / Indeed (qu'il n'ouvre pas), l'alerte sera marquée ❓ « dates à vérifier ».

### 5. APIs gratuites (optionnel)
- **Adzuna** : clé gratuite sur <https://developer.adzuna.com> → secrets `ADZUNA_APP_ID`, `ADZUNA_APP_KEY`.
- **France Travail** : application sur <https://francetravail.io> + abonnement à l'API « Offres d'emploi » → `FRANCETRAVAIL_CLIENT_ID`, `FRANCETRAVAIL_CLIENT_SECRET`. (Peu d'offres IB, mais gratuit.)

---

## Régler ton profil — `config/profile.yaml`

C'est **le** fichier à ajuster. Les réglages par défaut supposent : *fin des cours en avril 2027, 4 à 6 mois de stage ensuite*.

```yaml
window:
  earliest_start: 2027-04-15   # début au plus tôt
  latest_start:   2027-08-31   # début au plus tard
  min_months: 4
  max_months: 6
  asap_policy: digest           # « dès que possible » → résumé du matin seulement
  unknown_dates_policy: notify  # annonce sans date → alerte ❓ (mets « digest » pour plus de précision, moins de couverture)
```
- **Plus de précision** : `unknown_dates_policy: digest` (tu ne reçois plus que les offres dont les dates sont *confirmées* ; les autres arrivent dans le résumé du matin).
- **Plus de couverture** : garde `notify`, baisse `thresholds.notify`.
- `desks:` = tes métiers et leur poids ; `exclude_title:` = métiers à écarter ; `locations:` = villes.
- **Voir pourquoi une offre est acceptée/rejetée** :
  ```bash
  python -m radar explain --title "Stage Leveraged Finance" --location Paris \
      --text "Début du stage : janvier 2027, durée 6 mois"
  # → Verdict : DIGEST … début janvier 2027 hors fenêtre (trop tôt)
  ```

## Ajouter une entreprise — `config/sources.yaml`

1. **Détection automatique** : lance l'action **discover** (Actions → discover → URL de la page carrières + nom), ou en local :
   ```bash
   python -m radar discover "https://www.lazard.com/careers/" --name Lazard
   ```
   Il affiche le bloc YAML prêt à coller (Workday, Greenhouse, Lever, SmartRecruiters, Oracle HCM…).
2. Colle-le dans `sources.yaml`. Une entrée `type: auto` avec l'URL de la page carrières suffit souvent : le bot détecte l'ATS lui-même et re-détecte s'il change.
3. Si le site n'a pas d'API (Taleo, SuccessFactors, site maison, pages JavaScript) : ouvre l'onglet **Réseau** du navigateur sur la page des offres et cherche une URL contenant `myworkdayjobs`, `greenhouse`, `lever.co`, `oraclecloud`… → relance `discover` avec cette URL. À défaut, utilise `type: rss` / `sitemap` / `html`, ou les **alertes e-mail** (qui couvrent l'essentiel).

---

## Commandes

| Commande | Effet |
|---|---|
| `python -m radar run` | un passage complet (ce que fait GitHub toutes les 10 min) |
| `python -m radar run --dry-run --fresh` | simule sans rien envoyer ni enregistrer |
| `python -m radar doctor [--only NOM]` | teste les sources et montre leurs offres pertinentes |
| `python -m radar discover URL --name NOM` | détecte l'ATS et génère la config |
| `python -m radar explain --title … --text …` | explique le verdict sur une offre |
| `python -m radar digest` | envoie le résumé du jour |
| `python -m radar test-notify` | message de test Telegram / Discord |
| `python -m radar telegram-chat-id` | trouve ton `TELEGRAM_CHAT_ID` |
| `python -m pytest` | tests (plus de 100 cas) |

En local, mets tes secrets dans un fichier `.env` (modèle : `.env.example`).

---

## Limites à connaître (honnêtement)

- **Délai** : GitHub exécute les tâches planifiées « environ » toutes les 10 minutes ; en période de forte charge, le délai réel est souvent de 5 à 15 minutes, parfois plus. Pour une vraie précision à la minute, héberge le bot sur une machine à toi (voir plus bas).
- **60 jours d'inactivité** : GitHub désactive les workflows planifiés d'un dépôt public sans activité pendant 60 jours. Le bot appelle chaque nuit l'API pour se « réactiver » ; si tu ne reçois plus le résumé du matin, regarde l'onglet Actions et clique sur *Enable workflow*.
- **Dépôt privé** : 2 000 minutes gratuites par mois, soit environ un passage toutes les 20-30 minutes ; passe alors le cron de `radar.yml` à `*/30`. En dépôt public, ton profil (dates, desks) est visible ; les secrets, jamais.
- **Offres jamais publiées** : beaucoup de stages (surtout off-cycle) se pourvoient par le réseau, les chasseurs de têtes, les groupes WhatsApp. Le bot couvre le **public** ; pas le caché.
- **Dates absentes** : la majorité des annonces ne donnent pas la date de début. Elles arrivent en ❓ (ou dans le résumé si tu as choisi `digest`). Le bot ne devine jamais une date qui n'est pas écrite.
- **LinkedIn / Indeed** : volontairement pas scrapés (CGU, blocages, risque pour ton compte) → couverts par les alertes e-mail.
- **Sources à valider** : les URLs de pages carrières de `sources.yaml` sont des points de départ ; certaines changeront ou seront rendues en JavaScript. Lance `doctor` après chaque modification.
- **Pas de LLM** : l'extraction des dates est faite par règles (déterministe, gratuite, testée). Elle peut rater des formulations rares ; `explain` montre ce qui est lu.

## Héberger ailleurs (toujours gratuit)

- **Ton Mac** (précision ~1 min, doit rester allumé) — `launchd` :
  ```xml
  <!-- ~/Library/LaunchAgents/com.radar.plist ; charger : launchctl load ~/Library/LaunchAgents/com.radar.plist -->
  <plist version="1.0"><dict>
    <key>Label</key><string>com.radar</string>
    <key>ProgramArguments</key><array><string>/usr/bin/env</string><string>python3</string><string>-m</string><string>radar</string><string>run</string></array>
    <key>WorkingDirectory</key><string>/CHEMIN/internship-radar</string>
    <key>StartInterval</key><integer>300</integer>
    <key>StandardOutPath</key><string>/tmp/radar.log</string><key>StandardErrorPath</key><string>/tmp/radar.err</string>
  </dict></plist>
  ```
  (secrets dans `/CHEMIN/internship-radar/.env`).
- **Petite VM gratuite** (Oracle Cloud « Always Free »…) : `*/5 * * * * cd ~/internship-radar && python3 -m radar run` dans `crontab -e`, et `0 7 * * * … python3 -m radar digest` pour le résumé.
- **Docker** : `docker build -t radar . && docker run --rm --env-file .env -v $PWD/state:/app/state radar`.

## Sécurité & confidentialité

- Les secrets restent dans GitHub Secrets / `.env` (jamais dans le code). `.env` est ignoré par Git.
- Le bot lit ta boîte Gmail en **lecture seule** (`BODY.PEEK`, `readonly`) et uniquement les expéditeurs listés (LinkedIn, Indeed, WTTJ, etc.).
- Il n'ouvre que des pages publiques, avec un trafic très faible (une requête par offre nouvelle, sources espacées).

## Architecture (pour modifier le code)

```
radar/
  extract.py     dates de début + durée (FR/EN/DE) et comparaison à la fenêtre
  classify.py    stage ? lieu ? métier ? dates ? → niveau (TOP / GOOD / CHECK / DIGEST / DROP)
  pipeline.py    collecte → dédoublonnage → fiches détaillées → classement → alertes ; résumé quotidien
  store.py       base SQLite (offres vues, santé des sources, e-mails lus)
  sources/       un collecteur par type (ats.py, feeds.py, apis.py, email_alerts.py, auto.py, discover.py)
  notify/        Telegram, Discord, console
config/          profile.yaml (ton profil) · sources.yaml (entreprises surveillées)
```
Ajouter un type de source : une classe `Source` avec `fetch()` (et `enrich()` si la liste n'a pas la description), décorée `@register`.
