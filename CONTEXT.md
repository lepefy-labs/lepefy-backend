# Lepefy Backend — Contesto Progetto

## Panoramica

Backend FastAPI per il monitoraggio automatico di annunci su **Subito.it** e **Vinted.it**.
Identifica deal vantaggiosi per tre segmenti utenti distinti e invia notifiche email via Brevo.

Stack: FastAPI · Supabase (PostgreSQL) · Brevo (email) · Railway (hosting)

---

## Tre Segmenti Utenti

| Segmento       | Piattaforme         | Logica di Selezione                              | Scoring AI | Colonna DB (subscriptions)         |
|----------------|---------------------|--------------------------------------------------|------------|------------------------------------|
| **Flipper**    | Subito.it, Vinted.it | Score alto (margine di rivendita stimato da AI)  | Sì         | `is_collector = false/null`        |
| **Riparatori** | Vinted.it           | Annunci difettosi/rotti per ricambi              | No         | `is_collector = false/null`        |
| **Collezionisti** | Vinted.it        | Fascia prezzo specifica, tutte le condizioni     | No         | `is_collector = true`              |

---

## Schema Database

### Tabella `keywords`

| Colonna          | Tipo    | Note                                                    |
|------------------|---------|---------------------------------------------------------|
| `keyword`        | text    | Parola chiave di ricerca                                |
| `active`         | boolean | Se false, esclusa da tutti i job                        |
| `only_collector` | boolean | Se true, usata solo dal scanner collezionisti           |

**Migrazione:**
```sql
ALTER TABLE keywords
ADD COLUMN IF NOT EXISTS only_collector boolean DEFAULT false;
```

### Tabella `subscriptions`

| Colonna         | Tipo    | Note                                                          |
|-----------------|---------|---------------------------------------------------------------|
| `email`         | text    | Email del subscriber                                          |
| `keyword`       | text    | Keyword monitorata                                            |
| `active`        | boolean |                                                               |
| `min_threshold` | numeric | Prezzo minimo (€)                                             |
| `max_threshold` | numeric | Prezzo massimo (€)                                            |
| `is_collector`  | boolean | Se true, riceve notifiche dal job collector                   |
| `source`        | text    | Filtro opzionale piattaforma (`"Vinted.it"`, `null` = tutte) |

**Migrazione:**
```sql
ALTER TABLE subscriptions
ADD COLUMN IF NOT EXISTS is_collector boolean DEFAULT false;

ALTER TABLE subscriptions
ADD COLUMN IF NOT EXISTS source text DEFAULT NULL;
```

### Tabella `scan_results`

Colonne rilevanti: `id`, `keyword`, `title`, `price_raw`, `price_value`, `location`, `country`,
`url`, `date_listed`, `source`, `scored`, `score`, `margine_stimato`, `condition`, `image_url`, `body`.

### Tabella `notifications_log`

| Colonna          | Tipo | Note                              |
|------------------|------|-----------------------------------|
| `user_email`     | text |                                   |
| `scan_result_id` | uuid | FK → scan_results.id              |
| `sent_at`        | timestamptz |                           |

---

## Endpoint API

| Metodo | Path                          | Descrizione                                          |
|--------|-------------------------------|------------------------------------------------------|
| GET    | `/cron/scan`                  | Scansiona Subito.it per keyword flipper/riparatori   |
| GET    | `/cron/vinted-scan`           | Scansiona Vinted.it per keyword flipper              |
| GET    | `/cron/vinted-defective-scan` | Scansiona Vinted.it per annunci difettosi            |
| GET    | `/cron/scan-vinted-collector` | Scansiona Vinted.it per keyword collezionisti        |
| GET    | `/cron/score`                 | Scoring AI su annunci `scored=false`                 |
| GET    | `/cron/notify`                | Notifica flipper (Subito + Vinted)                   |
| GET    | `/cron/notify-defective`      | Notifica riparatori                                  |
| GET    | `/cron/notify-collector`      | Notifica collezionisti                               |
| GET    | `/cron/market-scan`           | Aggiorna market_snapshots                            |

Tutti i cron endpoint richiedono `?secret=CRON_SECRET` come query parameter.

---

## Logica per Modulo

### `app/scraper/scanner.py` — Subito.it

- Legge keyword con `active=true` **e** `only_collector != true`:
  ```python
  .or_("only_collector.eq.false,only_collector.is.null")
  ```
- Salva annunci con `scored=false` per il successivo job di scoring.

### `app/scraper/vinted_scanner.py` — Vinted Flipper

- Stessa esclusione keyword collector:
  ```python
  .or_("only_collector.eq.false,only_collector.is.null")
  ```
- Salva con `scored=false`.

### `app/scraper/vinted_defective_scanner.py` — Vinted Riparatori

- Filtra per `status_id` difettosi (rotto, per ricambi).
- Salva con `scored=true` (bypass scorer).

### `app/scraper/vinted_collector_scanner.py` — Vinted Collezionisti

**Keywords:** solo `keywords WHERE only_collector = true AND active = true`.

**Fasce prezzo:** unione di tutti i range `(min_threshold, max_threshold)` dalle
subscription attive con `is_collector = true` per la stessa keyword.

#### TITLE_BLACKLIST

Parole che, se presenti nel titolo dell'annuncio (case-insensitive), causano lo scarto
dell'annuncio prima del salvataggio. Evita il salvataggio di accessori moda, oggettistica,
libri e custodie che contengono keyword generiche (es. "canon", "nikon", "olympus") nel titolo
ma non sono fotocamere o oggetti da collezione.

```python
TITLE_BLACKLIST = {
    "zaino", "borsa", "orologio", "cinturino", "occhiali", "profumo",
    "scarpe", "cappello", "giacca", "pantaloni", "gonna", "vestito",
    "felpa", "maglione", "camicia", "stampa", "poster", "libro",
    "manuale", "custodia", "cover", "skin",
}
```

Funzione di controllo:
```python
def _is_blacklisted(title: str) -> bool:
    title_lower = title.lower()
    return any(word in title_lower for word in TITLE_BLACKLIST)
```

#### Pipeline di filtraggio per annuncio

Per ogni annuncio restituito dall'API Vinted, il filtro applicato è nell'ordine:

1. **URL presente** — scarta se manca
2. **Keyword nel titolo** — tutti i token della keyword devono essere nel titolo (`rejected++`)
3. **TITLE_BLACKLIST** — scarta se il titolo contiene almeno una parola della blacklist (`blacklisted++`)
4. **price_value presente** — scarta se il prezzo non è parsabile
5. **Prezzo nella fascia** — deve rientrare in almeno una subscription range (`rejected++`)
6. **Country in ALLOWED_COUNTRIES** — dopo fetch user API, scarta se fuori IT/FR (`incomplete++`)

#### Contatori nel risultato

Per ogni keyword scansionata il dizionario di risposta include:

| Campo         | Descrizione                                      |
|---------------|--------------------------------------------------|
| `found`       | Annunci totali restituiti dall'API Vinted        |
| `new`         | Nuovi annunci salvati in scan_results            |
| `updated`     | Annunci esistenti con prezzo aggiornato ≥ 15%   |
| `skipped`     | Annunci esistenti con prezzo invariato           |
| `rejected`    | Scartati per keyword/prezzo fuori range          |
| `blacklisted` | Scartati per TITLE_BLACKLIST                     |
| `incomplete`  | Scartati per paese fuori ALLOWED_COUNTRIES       |

Il summary globale (`run_vinted_collector_scan`) include `total_blacklisted` come somma
dei `blacklisted` di tutti i keyword result.

**Inserimento:** `scored=True` — i collezionisti cercano oggetti specifici, non margine.
**Deduplication:** upsert su `url` con `ignore_duplicates=True`.
**Reinvio:** se prezzo cala ≥ 15%, il record `notifications_log` viene eliminato
per consentire una nuova notifica.

### `app/scraper/notifier_collector.py` — Notifiche Collezionisti

- Raggruppa subscription attive (`is_collector=true`) per email.
- Per ogni email recupera annunci `scored=true, source=Vinted.it` per le keyword abbonate.
- Esclude ID già in `notifications_log` per quell'email.
- Applica filtro `source` se impostato sulla subscription.
- Ordina per `price_value ASC` e prende i primi `MAX_DEALS = 5`.
- Invia email HTML con card annunci (pulsante CTA indigo `#4f46e5`, badge condizione).
- Inserisce in `notifications_log` **solo dopo** invio email riuscito.

### `app/scraper/scorer.py` — Scoring AI

- Legge annunci con `scored=false`.
- Chiama eBay API per prezzo di mercato + Claude Haiku per valutazione.
- **Non tocca** annunci con `scored=true` (collector e defective).

---

## Note Operative

- `ALLOWED_COUNTRIES = {"IT", "FR"}` — solo annunci da venditori italiani o francesi.
  Aggiungere `"DE"`, `"ES"` se si vuole espandere la copertura geografica.
- Il job collector non ha filtro `status_id`: include annunci in qualsiasi condizione
  (ottimo, buono, accettabile, difettoso).
- Le keyword con `only_collector=true` sono escluse dai job Subito e Vinted flipper
  tramite il filtro `.or_("only_collector.eq.false,only_collector.is.null")`.
- La TITLE_BLACKLIST è un set Python: la ricerca è `O(1)` per parola, `O(n_words)` per titolo.
  Aggiungere parole direttamente alla costante `TITLE_BLACKLIST` in `vinted_collector_scanner.py`.

---

## Variabili d'Ambiente

| Variabile               | Utilizzo                          |
|-------------------------|-----------------------------------|
| `SUPABASE_URL`          | URL istanza Supabase              |
| `SUPABASE_SERVICE_KEY`  | service_role key (bypassa RLS)    |
| `CRON_SECRET`           | Token autenticazione cron job     |
| `BREVO_API_KEY`         | API key Brevo per email           |
| `EBAY_APP_ID`           | eBay API per prezzi mercato       |
| `ANTHROPIC_API_KEY`     | Claude Haiku per scoring          |
| `EBAY_VERIFICATION_TOKEN` | GDPR endpoint eBay              |
| `SCRAPERAPI_KEY`        | Proxy per Subito.it               |
