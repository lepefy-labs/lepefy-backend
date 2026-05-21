# Lepefy — Backend API Context
> Aggiornato al 20/05/2026 — versione per Claude Code

## Descrizione
Lepefy è uno scanner AI-powered per marketplace C2C italiani (Subito.it, Vinted.it).
Monitora annunci di elettronica/fotografia usata, valuta il margine di rivendita
con Claude Haiku e notifica gli utenti Premium via email quando trova affari.
Raccoglie dati statistici di mercato in una tabella separata per analisi future su
prezzi, time-to-sell e trend per categoria.
Genera automaticamente caption social (Instagram, TikTok, Facebook) sui top deal
per supportare la comunicazione di Lepefy sui social media.

---

## Stack Tecnologico

| Componente | Tecnologia |
|---|---|
| Backend API | FastAPI (Python) su Railway |
| Database | Supabase (PostgreSQL) |
| Scraping | ScraperAPI (parametrizzabile) |
| AI Scoring | Claude Haiku (claude-haiku-4-5-20251001) |
| Email | Brevo API |
| Deploy | Railway Hobby ($5/mese) + cron job |
| Landing page | lepefy.it / lepefy.com (live) |

---

## Struttura Progetto

```
app/
└── scraper/
    ├── scanner.py                — fetch Subito, salvataggio raw in scan_results (no AI)
    ├── scorer.py                 — AI scoring batch su annunci non ancora scored
    ├── notifier.py               — notifiche email flipper (score >= 7, include_defective=False)
    ├── notifier_defective.py     — notifiche email riparatori (condition='Non del tutto funzionante')
    ├── notifier_collector.py     — notifiche email collezionisti (is_collector=True)
    ├── market_scanner.py         — scanner di mercato (no AI), salva in market_snapshots
    ├── market_analytics.py       — query analitiche su market_snapshots
    ├── content_generator.py      — genera caption social dai top deal, invia via email
    ├── vinted_scanner.py         — fetch Vinted.it, salvataggio raw in scan_results (no AI)
    ├── vinted_defective_scanner.py — fetch Vinted annunci difettosi per riparatori
    └── vinted_collector_scanner.py — fetch Vinted per collezionisti (only_collector=True)
main.py                           — FastAPI endpoints
```

---

## Variabili d'Ambiente (Railway)

```
SCRAPERAPI_KEY         — API key ScraperAPI
SCRAPER_MODE           — scraperapi | webshare | direct
WEBSHARE_PROXY_USER    — credenziali Webshare (se SCRAPER_MODE=webshare)
WEBSHARE_PROXY_PASS
SUPABASE_URL           — URL base progetto Supabase (senza /rest/v1)
SUPABASE_SERVICE_KEY   — service_role key (bypassa RLS)
ANTHROPIC_API_KEY      — API key Claude
BREVO_API_KEY          — API key Brevo
EMAIL_FROM             — mittente email (es. noreply@lepefy.it)
EMAIL_FROM_NAME        — nome mittente (es. Lepefy)
CRON_SECRET            — token per proteggere endpoint cron
NOTIFY_EMAIL_OVERRIDE  — email test per debug notifiche
CONTENT_EMAIL_TO       — email destinatario contenuti social (content_generator)
SCORE_BATCH_SIZE       — numero annunci per ciclo scorer (default: 50)
```

---

## Schema Database Supabase

### keywords
```sql
id uuid PK,
keyword text UNIQUE,
active boolean DEFAULT true,
include_defective boolean DEFAULT false,   -- keyword può produrre annunci difettosi (Vinted)
only_collector boolean DEFAULT false,      -- keyword riservata al segmento collezionisti
created_at timestamp
```
Tutte le keyword sono in **lowercase**.

Migrazione:
```sql
ALTER TABLE keywords ADD COLUMN IF NOT EXISTS only_collector boolean DEFAULT false;
```

Filtri lettura per modulo:
- `scanner.py` / `vinted_scanner.py`: `active=true AND (only_collector=false OR only_collector IS NULL)`
- `vinted_collector_scanner.py`: `active=true AND only_collector=true`

### scan_results
```sql
id uuid PK,
keyword text,
title text,
price_raw text,
price_value numeric,
location text,
url text UNIQUE,
date_listed timestamp,
source text,             -- 'Subito.it' | 'Vinted.it'
score integer,
margine_stimato numeric,
motivazione text,
rischi text,
scored boolean DEFAULT false,  -- false = in coda per scorer; true = già processato
country text,            -- da Vinted (es. "IT"); NULL per Subito
body text,               -- descrizione annuncio da Vinted; NULL per Subito
image_url text,          -- prima foto da Vinted; NULL per Subito
condition text,          -- condizione da Vinted (es. "Non del tutto funzionante"); NULL per Subito
created_at timestamp
```
`url` è UNIQUE per deduplicazione tra piattaforme.

### subscriptions
```sql
id uuid PK,
email text,
keyword text,            -- lowercase, deve matchare keywords.keyword
min_threshold numeric,
max_threshold numeric,
active boolean DEFAULT true,
include_defective boolean DEFAULT false,  -- True = utente vuole annunci difettosi (riparatori)
is_collector boolean DEFAULT false,       -- True = utente collezionista
source text DEFAULT NULL,                 -- filtra per piattaforma specifica se valorizzato
created_at timestamp
```

Migrazioni:
```sql
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS is_collector boolean DEFAULT false;
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS source text DEFAULT NULL;
```

Logica `include_defective` — uno stesso utente può avere due subscription parallele:

| email | keyword | include_defective | min | max |
|---|---|---|---|---|
| user@x.it | iphone | false | 50 | 300 |
| user@x.it | iphone | true  | 30 | 150 |

- `include_defective=false` → notifier standard (flipper)
- `include_defective=true` → notifier_defective (riparatori)
- `is_collector=true` → notifier_collector (collezionisti)

### notifications_log
```sql
id uuid PK,
subscription_id uuid FK → subscriptions.id,
scan_result_id uuid FK → scan_results.id,
sent_at timestamp
```
Inserito SOLO dopo invio email riuscito. Traccia le notifiche per utente.

### ai_usage_log
```sql
id uuid PK,
keyword text,            -- "content_generator" per le righe del content job
input_tokens integer,
output_tokens integer,
cost_usd numeric,
deals_scored integer,
created_at timestamp
```

### market_snapshots
```sql
id bigserial PK,
keyword text NOT NULL,
categoria text,          -- fotografia | audio_hifi | strumenti_musicali | elettronica | videogiochi
marca text,
modello text,
source text DEFAULT 'Subito.it',
price_value numeric,
location text,           -- "Città, Regione"
condizione text,         -- da features Subito: Nuovo | Ottime condizioni | ecc.
first_seen_at timestamptz,
last_seen_at timestamptz,
is_sold boolean DEFAULT false,
sold_at timestamptz,
_url_ref text UNIQUE     -- solo per dedup interno, non esposto via API
```
Popolata esclusivamente da `market_scanner.py`. Nessun testo creativo né dato personale.

---

## Endpoint API

| Endpoint | Auth | Descrizione |
|---|---|---|
| GET / | pubblico | Health check |
| GET /test-scan?q= | pubblico | Scan senza salvare |
| GET /cron/scan | CRON_SECRET | Fetch Subito → scan_results (no AI) |
| GET /cron/vinted-scan | CRON_SECRET | Fetch Vinted.it → scan_results (no AI) |
| GET /cron/vinted-defective-scan | CRON_SECRET | Fetch Vinted annunci difettosi per riparatori |
| GET /cron/scan-vinted-collector | CRON_SECRET | Fetch Vinted per collezionisti (only_collector=true) |
| GET /cron/score | CRON_SECRET | AI scoring batch su annunci scored=false |
| GET /cron/notify | CRON_SECRET | Notifiche flipper (score>=7, include_defective=False) |
| GET /cron/notify-defective | CRON_SECRET | Notifiche riparatori (condition filter) |
| GET /cron/notify-collector | CRON_SECRET | Notifiche collezionisti (is_collector=true) |
| GET /cron/market-scan | CRON_SECRET | Scan mercato → market_snapshots |
| GET /cron/content | CRON_SECRET | Caption social top deal → email interna |
| GET /market/price-stats | pubblico | Statistiche prezzo per modello |
| GET /market/time-to-sell | pubblico | Tempo medio vendita per modello |
| GET /market/price-trend | pubblico | Andamento prezzo nel tempo |
| GET /market/active | pubblico | Annunci attivi filtrabili |

### Autenticazione CRON_SECRET
Il token va come **query parameter**, non come header:
```
GET /cron/scan?secret=IL_TUO_TOKEN
```

### Parametri endpoint analytics
```
/market/price-stats?modello=A7 III&condizione=Ottime condizioni&giorni=90
/market/time-to-sell?modello=PS5&giorni=180
/market/price-trend?modello=A7 III&giorni=90&bucket=week   (bucket: day|week|month)
/market/active?categoria=fotografia&marca=Sony&modello=A7 III&condizione=...&limit=50
```

---

## Segmenti Utenti

Lepefy serve tre profili distinti, ognuno con un pipeline dedicato:

| Segmento | Cerca | Logica notifica | Scanner | Notifier |
|---|---|---|---|---|
| **Flipper** | Deal con margine alto | score >= 7, ordine per score×margine | scanner.py + vinted_scanner.py + scorer.py | notifier.py |
| **Riparatori** | Articoli difettosi Vinted | condition='Non del tutto funzionante', ordine prezzo crescente | vinted_defective_scanner.py | notifier_defective.py |
| **Collezionisti** | Articoli specifici (no score) | fascia prezzo, source opzionale, ordine prezzo crescente | vinted_collector_scanner.py | notifier_collector.py |

Distinzione a livello DB:
- keyword con `only_collector=true` → riservate al pipeline collezionisti; ignorate da scanner.py e vinted_scanner.py
- subscription con `is_collector=true` → processed da notifier_collector.py
- subscription con `source` valorizzato → filtro piattaforma aggiuntivo nel notifier

Caso d'uso reale: **Andrea** (beta tester), riparatore iPhone, vuole iPhone "non del tutto funzionante" su Vinted per ricambi.

---

## Logica Scanner (scanner.py)

### Flusso _scan_keyword
1. Legge keyword con `active=true AND (only_collector=false OR only_collector IS NULL)`
2. Fetch pagina Subito via _fetch_html (router per SCRAPER_MODE)
3. Parsing `__NEXT_DATA__` JSON iniettato da Next.js
4. Recupera URL+prezzi già in DB per questa keyword
5. Per ogni annuncio nuovo: pre-filtro keyword in titolo o primi 200 char body
6. Upsert in scan_results con `scored=false` (coda per scorer)
7. Se prezzo calato >= 15%: aggiorna DB + resetta notifications_log
8. Zero chiamate AI in questa fase

### SCRAPER_MODE
```python
"scraperapi"  # default; country_code=it non incluso nel piano attuale
"webshare"    # proxy Webshare, round-robin su 10 IP statici
"direct"      # chiamata diretta, solo per sviluppo locale
```

---

## Logica Vinted Scanner (vinted_scanner.py)

Parallelo a `scanner.py` per Vinted.it. Stessa tabella `scan_results`, `source='Vinted.it'`.

### Differenze rispetto a Subito
- **Parsing**: libreria Python non ufficiale (reverse-engineering endpoint interni Vinted)
- **Fee**: ~5% + €0,70 paga l'acquirente, non il venditore → il venditore incassa il prezzo pieno. Claude usa il prezzo listato come prezzo netto nel calcolo del margine.
- **Campi aggiuntivi**: `country`, `body`, `image_url` (NULL per Subito)
- **Filtro keyword**: `active=true AND (only_collector=false OR only_collector IS NULL)`

### Logica skip annunci incompleti
Se `country` o `body` mancano → annuncio skippato. Il response include un campo `incomplete`.

### Pulizia annunci incompleti già in DB
```sql
DELETE FROM scan_results
WHERE source = 'Vinted.it'
AND (country IS NULL OR body IS NULL OR body = '');
```

---

## Logica Scorer (scorer.py)

### Flusso _run_score_job
1. Legge fino a `SCORE_BATCH_SIZE` annunci con `scored=false`
2. Per ogni annuncio: chiama Claude Haiku
   - Verifica pertinenza keyword
   - Valuta componenti separatamente (corpo + obiettivi + accessori)
   - margine = valore_rivendita - prezzo_acquisto - spedizione (no commissioni piattaforma)
   - Sanity check: margine max 1.5x prezzo acquisto
   - Ritorna: score 1-10, verdict (AFFARE/OK/EVITA), margine_stimato, motivazione, rischi
3. Scarta non pertinenti (score=1) o margine <= 0
4. Aggiorna scan_results: score, margine, motivazione, rischi, `scored=true`
5. Log token su ai_usage_log

### SCORE_BATCH_SIZE
Default 50. Per smaltire backlog: alzare a 100 su Railway temporaneamente.
Batch alti aumentano il rischio di errori 529 (Claude overload).

### Errori nel score job
Tasso ~12% su batch pieno (6/50 in produzione). Cause: errore 529 Claude o body
inatteso che rompe il parsing. Gli annunci con errore restano `scored=false` e
vengono riprovati al ciclo successivo.

---

## Logica Notifier (notifier.py)

### Flusso _run_notify_job
1. Legge subscription con `active=true AND include_defective=false`
2. Per ogni subscription:
   - Recupera deal già notificati (notifications_log)
   - Query scan_results: keyword ilike + fascia prezzo + `score >= 7`
   - Filtra deal non ancora notificati a questo utente
   - Ordina per `score × margine_stimato` (i più ricchi in cima)
   - Max 5 annunci per email (i restanti al ciclo successivo)
   - Invia via Brevo
   - Inserisce in notifications_log SOLO dopo invio riuscito

### Soglia score
`score >= 7` nel notifier. Motivazione: 7-8 = deal genuinamente interessanti,
9-10 = rari ma ottimi. Il content_generator usa 6 (bar più basso, perché serve
solo che il deal sia presentabile, non che sia la scelta migliore per un acquirente).

---

## Logica Notifier Defective (notifier_defective.py)

Per utenti che cercano articoli in condizione specifica su Vinted (segmento riparatori).

```python
DEFECTIVE_CONDITION = "Non del tutto funzionante"
```

### Differenze rispetto al notifier standard
- Legge subscription con `include_defective=true`
- Filtra `scan_results`: `source='Vinted.it' AND condition=DEFECTIVE_CONDITION`
- Nessun filtro su score o margine (NULL per articoli difettosi)
- Ordine per prezzo crescente (logica repair flipping: più economico = più interessante)
- Max 5 annunci per email

### Pulizia annunci con condizione errata
```sql
DELETE FROM notifications_log
WHERE scan_result_id IN (
    SELECT id FROM scan_results
    WHERE condition = 'Discrete' AND source = 'Vinted.it'
);
DELETE FROM scan_results
WHERE condition = 'Discrete' AND source = 'Vinted.it';
```

---

## Logica Notifier Collector (notifier_collector.py)

Per collezionisti che cercano articoli specifici senza interesse per il margine di rivendita.

- Legge subscription con `is_collector=true`
- Filtra `scan_results`: keyword + fascia prezzo (min/max threshold)
- Se `subscription.source` è valorizzato → aggiunge filtro `scan_results.source = subscription.source`
- Nessun filtro su score, condition o margine
- Ordine per prezzo crescente
- Max 5 annunci per email
- Email con pulsante indigo (distinta da flipper verde e riparatori rosso)

---

## Logica Vinted Collector Scanner (vinted_collector_scanner.py)

- Legge keyword con `active=true AND only_collector=true`
- Fetch Vinted senza filtro condizione (tutti gli annunci indipendentemente dallo stato)
- Pre-carica fasce prezzo da subscriptions con `is_collector=true`, raggruppate per keyword
- Filtra annunci: price_value deve rientrare nella fascia di almeno una subscription
- Salva in scan_results con `scored=true` (bypass scorer — collezionisti valutano l'oggetto di persona)
- Stessa logica price-drop-reset degli altri scanner Vinted
- Endpoint: `GET /cron/scan-vinted-collector`

---

## Logica Content Generator (content_generator.py)

Modulo operativo interno — non espone dati agli utenti.

```python
TOP_N_DEALS    = 3
MIN_SCORE      = 6
HOURS_LOOKBACK = 24
```

### Flusso run_content_job
1. Legge top `TOP_N_DEALS` deal delle ultime 24h con score >= 6
2. Per ogni deal: Claude Haiku genera caption per Instagram (max 150 parole + 10-15 hashtag),
   TikTok (max 80 parole + 5-8 hashtag), Facebook (max 120 parole + 3-5 hashtag)
3. Log token su `ai_usage_log` con `keyword="content_generator"`
4. Assembla email HTML e invia via Brevo a `CONTENT_EMAIL_TO`

Risposta Claude forzata in JSON puro. Se nessun deal soddisfa i criteri: `{"status":"skip"}`.

---

## Logica Market Scanner (market_scanner.py)

- Zero AI — zero costo Anthropic
- Keyword fisse in `MARKET_TAXONOMY` (non da Supabase)
- Fino a 100 annunci per keyword con paginazione; pausa 8s tra keyword
- Proprietario esclusivo di `market_snapshots`

### Tassonomia (`MARKET_TAXONOMY`)
```python
fotografia:          sony a7 / a7 iii / a7 iv / a7r / a6000 / a6400,
                     fujifilm x-t / x100, canon eos r / r6,
                     nikon z6 / z7, olympus om-d, panasonic lumix g
audio_hifi:          amplificatore valvolare, giradischi, casse acustiche,
                     yamaha hs8, focal alpha, sennheiser hd, audio technica at
strumenti_musicali:  chitarra fender stratocaster, chitarra gibson les paul,
                     sintetizzatore moog, roland juno, korg minilogue, pianoforte digitale
elettronica:         macbook pro m1/m2, macbook air m2, ipad pro, iphone 14/15
videogiochi:         ps5, nintendo switch oled, xbox series x, steam deck
```

**Keyword con saved:0 da investigare:** `fujifilm x-t`, `chitarra gibson les paul`, `focal alpha`
— keyword multi-token con mismatch ordine token nel titolo.

### Filtri di pertinenza (`_is_relevant`)
1. Keyword nel titolo (tutti i token presenti)
2. Blacklist accessori — solo se keyword assente dal titolo
3. Price range per categoria (soglie conservative)

### Nota su is_sold
`is_sold=True` è inferito dalla scomparsa dal feed, non da conferma reale.
Subito ruota i risultati → molti "venduti" sono usciti dalla prima pagina.
Trattare come stima, non come fatto. Motivo principale per cui eBay Completed
Listings è prioritaria.

---

## Cron Schedule (Railway, UTC — sottrarre 2h per orario IT in CEST)

```
Scanner Subito:       0 13 * * *  (15:00 IT)   0 20 * * *  (22:00 IT)
Scorer:              10 13 * * *  (15:10 IT)  10 20 * * *  (22:10 IT)
Notify:              15 13 * * *  (15:15 IT)  15 16 * * *  (18:15 IT)
                     15 18 * * *  (20:15 IT)  30 20 * * *  (22:30 IT)
Market scanner:      30 18 * * *  (20:30 IT)  — 1x al giorno
Content generator:    0  6 * * *  ( 8:00 IT)  — 1x al giorno
```

**Piano Railway:** Hobby ($5/mese) — necessario per cron job (free plan: 0 cron).
**Affidabilità:** down globale il 19/05/2026. Status: https://status.railway.com
**Render** identificato come alternativa con meno downtime storico.

---

## Keyword Operative (scanner + notifier)

Tutte in **lowercase**:

```
canon eos            — più produttiva; ~23% costo AI per scan
nikon d7500
nikon d5600
nikon d7000
leica m              — sostituisce "leica" (~22 rejected/30)
obiettivo tamron
obiettivo canon      — standalone Canon (no mount specifico)
obiettivo canon rf   — mount RF; ~24 rejected/30, molto rumorosa
obiettivo canon ef   — mount EF; ~26 rejected/30, molto rumorosa
olympus om
iphone               — ~17 rejected/30, alta produzione
alto ts415           — nicchia audio, pochissimi annunci
thinkpad
```

**Keyword dismesse:** `leica` (→ `leica m`), `fotocamera leica`, `accessori canon`

**Costo scan misurato:** $0.097 con 12 keyword (picco). Media $0.07/scan.
`iphone` + `canon eos` = ~52% del costo totale.

---

## Decisioni Tecniche Chiave

| Decisione | Motivazione |
|---|---|
| render=true rimosso da ScraperAPI | Causava 500 — `__NEXT_DATA__` presente nell'HTML statico |
| country_code=it rimosso | Non incluso nel piano ScraperAPI attuale |
| Keyword lowercase ovunque | Evita mismatch case-sensitive tra tabelle |
| ilike invece di eq nel notifier | Confronto case-insensitive su keyword |
| Scanner e scorer separati | Scanner puro (no AI, no costo); scorer batch configurabile |
| Scoring condiviso per keyword | Costo AI fisso indipendente dal numero di utenti |
| Skip URL già in DB | Risparmio ~80% token |
| Pre-filtro keyword prima di Claude | Risparmio ~76% token |
| CRON_SECRET come query param | Il backend legge `?secret=`, non header Authorization |
| Brevo invece di Resend | SMTP bloccato su Railway; Resend richiedeva dominio a pagamento |
| Margine senza commissioni piattaforma | Decisione lasciata al venditore |
| Sanity check margine 1.5x | Evita margini gonfiati su kit con accessori |
| 5 annunci max per email | Evita overload utente; coda smaltita ai cicli successivi |
| notifications_log per utente | Ogni utente riceve notifiche indipendenti sugli stessi deal |
| market_snapshots separata da scan_results | Operativo vs analitico — scan_results può fare upsert/delete senza perdere storico |
| Nessuna AI in market_scanner | Zero costo Anthropic per dati grezzi |
| Blacklist accessori condizionale | Solo se keyword assente dal titolo |
| marca/modello deterministici | Mapping hardcodato, zero AI |
| is_sold inferito dalla scomparsa | Approssimazione accettabile; da affinare con eBay |
| content_generator separato da notifier | Destinatario diverso (interno vs utenti) |
| keyword="content_generator" in ai_usage_log | Separa costi content job da costi scanner |
| score >= 7 nel notifier | Bar più alto per utenti paganti (content_generator usa 6) |
| include_defective come tipo, non add-on | Due subscription parallele per stesso utente: normali + difettosi, soglie prezzo diverse |
| only_collector su keywords | Scanner standard ignora keyword collezionisti e viceversa |
| is_collector + source su subscriptions | Segmento collezionisti con filtro piattaforma opzionale |
| Vinted: libreria Python non ufficiale | Vinted non espone API pubbliche |
| Fee Vinted a carico dell'acquirente | Venditore incassa prezzo pieno → Claude usa prezzo listato come netto |
| scan_results unificata Subito + Vinted | Campo `source` distingue; scorer processa coda unificata |
| notifier_defective separato | Logica distinta: condition filter vs score×margine |
| notifier_collector separato | Logica distinta: nessun scoring AI, filtro source opzionale, email indigo |
| scored=True in collector scanner | Collezionisti valutano l'oggetto di persona — AI scoring non utile |
| Filtro prezzo nel collector scanner | Evita di salvare annunci fuori da ogni fascia subscription attiva |
| Alternative a eBay Completed Listings non valutate | Marketplace Insights API, Averageprice.io, Keepa, scraping sold Subito — nessuna analizzata né testata |

---

## Pricing Strategy

Documento formale: `lepefy_pricing_strategy.docx`

| Tier | Prezzo | Target | Stato |
|---|---|---|---|
| Beta Tester | €9,99/mese (bloccato a vita) | 26 subscription attive | Attivo |
| Lancio pubblico | €14,99/mese | Nuovi utenti | Roadmap |
| Pro | €39,99/mese | Semi-pro / dealer | Roadmap Phase 2 |

**Prerequisiti per lancio a €14,99:**
- eBay Completed Listings integration
- Dashboard self-serve (gestione keyword e soglie)
- 5+ case study documentati di deal chiusi

**Beta tester reali:** ~6 su 26 subscription (il resto sono amici/familiari).

---

## Costi Attuali (mensili stimati)

| Servizio | Costo |
|---|---|
| Railway Hobby | ~$5 |
| ScraperAPI | $49 |
| Claude Haiku API | ~$4-8 (media $4.20/mese; picco $0.097/scan con 12 keyword) |
| Brevo | €0 (free tier) |
| Supabase | €0 (free tier) |
| Domini lepefy.it + .com | ~€2.50 |
| **Totale** | **~€60-70/mese** |

Break-even: 7 utenti a €9,99 / 5 utenti a €14,99.

---

## Roadmap

### Phase 2 — Priorità (in ordine)

**1. eBay Completed Listings API** ← prima cosa
- API: eBay Finding API (gratuita), `findCompletedItems` + `SoldItemsOnly=true`
- Flusso: marca/modello → query eBay → mediana prezzi - 12% fee → anchor per Claude → fallback AI se <5 risultati
- Le fee eBay (~12%) vanno nettate PRIMA di calcolare il margine

**2. Raccolta feedback deal chiusi**
Necessario per i 5 case study del lancio. Opzioni in ordine di sforzo:
- Email personale ai beta (adesso, costo zero)
- Link "Ho comprato questo affare" nell'email → form precompilato
- Tabella `deals_closed` in Supabase con dashboard (Phase 2)

**3. Segmento collezionisti** ← implementato
Richiede: `vinted_collector_scanner.py`, `notifier_collector.py`, nuovi endpoint,
nuovi campi DB (`only_collector` su keywords, `is_collector` + `source` su subscriptions),
filtro `only_collector=false` in `scanner.py` e `vinted_scanner.py`.

**4. Supabase Auth + Dashboard utente**
Login email/Google, gestione keyword e soglie per singolo utente.

**5. Webhook Stripe**
Automazione onboarding paganti. Attivare a ~10 utenti paganti.

### Phase 2b (market data)
- Dashboard analytics su market_snapshots
- Validazione stime AI con prezzi eBay reali
- Report di mercato mensili (potenziale B2B)

### Phase 3
- Notifiche Telegram
- Playwright + proxy residenziali (sostituzione ScraperAPI)
- Wallapop
- Alert predittivi stagionali (richiede 6-12 mesi di dati)

---

## Note Operative

```sql
-- Svuota scan_results e notifications_log
TRUNCATE TABLE scan_results CASCADE;

-- Reset notifiche per utente
DELETE FROM notifications_log
WHERE subscription_id IN (SELECT id FROM subscriptions WHERE email = 'xxx');

-- Disattivare keyword rumorosa
UPDATE keywords SET active = false WHERE keyword = 'xxx';
UPDATE subscriptions SET active = false WHERE keyword = 'xxx';

-- Pulizia annunci Vinted con condizione errata
DELETE FROM notifications_log
WHERE scan_result_id IN (
    SELECT id FROM scan_results WHERE condition = 'Discrete' AND source = 'Vinted.it'
);
DELETE FROM scan_results WHERE condition = 'Discrete' AND source = 'Vinted.it';

-- Pulizia annunci Vinted incompleti
DELETE FROM scan_results
WHERE source = 'Vinted.it' AND (country IS NULL OR body IS NULL OR body = '');

-- Svuota market_snapshots
TRUNCATE TABLE market_snapshots;
```

**Migrazioni DB per segmento collezionisti:**
```sql
ALTER TABLE keywords ADD COLUMN IF NOT EXISTS only_collector boolean DEFAULT false;
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS is_collector boolean DEFAULT false;
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS source text DEFAULT NULL;
```

**Altre note:**
- Aggiungere keyword operativa: insert in `keywords` + insert in `subscriptions` per ogni utente (lowercase)
- Keyword collezionisti: insert con `only_collector=true`; subscription con `is_collector=true`
- Aggiungere keyword di mercato: `MARKET_TAXONOMY` + `KEYWORD_BRAND_MODEL` + `PRICE_RANGES` in market_scanner.py
- Smaltire backlog scorer: `SCORE_BATCH_SIZE=100` su Railway temporaneamente, poi riportare a 50
- Logo email: `https://osonphsavryefwmlhkyv.supabase.co/storage/v1/object/public/assets/lepefy-logo-email.png`
- Debug content generator: `GET /cron/content?secret=TOKEN` → se `{"status":"skip"}` non ci sono deal score>=6 nelle ultime 24h
- Debug notifier-defective: verificare che esistano `source='Vinted.it' AND condition='Non del tutto funzionante'` non ancora notificati ad Andrea
- Debug notifier-collector: verificare che esistano keyword con `only_collector=true` e subscription con `is_collector=true`
- Subscriptions al 20/05/2026: 26 totali (~6 beta reali, restanti amici/familiari)
