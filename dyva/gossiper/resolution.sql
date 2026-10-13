-- gossiper resolution — the UNIFIED VIEW the router reads.
--
-- Our own probes (MEASURED) and peers' (ATTESTED) live in SEPARATE tables so attested data
-- never touches measured truth (quarantine): a peer can't overwrite what you measured, and a
-- bad attester's reports drop wholesale with one DELETE that can't scratch host_measured.
-- Mirrors graflex's host / bad_hosts two-table, last-wins split.
--
-- Conflict resolution per (host, model):
--   * a HONEYPOT verdict (measured OR attested) is ONE-WAY sticky and WINS — trusted, so the
--     host is skipped WITHOUT contact (re-probing a claimed honeypot is the hazard). A measured
--     honeypot outranks an attested one only for provenance; both say "skip".
--   * otherwise the MOST-RECENT row wins. Combined with re-measure-on-use this reproduces
--     fill-blanks for free: a host you use has the freshest measured row (yours wins); a host
--     you don't, attested fills the blank; a fresh peer update beats your stale own row.
-- Escape hatch for a poisoned/stale honeypot: invalidate the attester
--   (DELETE FROM hosts_attested WHERE attester = ?) — or age attested rows out. Never re-contact.

-- Our own probes. In production this is dyva's host_status (verdict derived from state/smoke,
-- ts from updated_at). Modeled minimally here so the resolution can be tested in isolation.
CREATE TABLE IF NOT EXISTS host_measured (
  host    TEXT NOT NULL,
  model   TEXT NOT NULL,
  verdict TEXT NOT NULL,          -- 'pass' (real endpoint) | 'honeypot'
  ts      TEXT NOT NULL,          -- ISO8601 UTC
  PRIMARY KEY (host, model)
);

-- Peer attestations the gossiper pulled down. QUARANTINED from host_measured.
--   attester = who reported it (their IP / id — pushed down so a report is invalidatable later).
--   source   = the sha1(source url) namespace the row arrived on.
CREATE TABLE IF NOT EXISTS hosts_attested (
  host        TEXT NOT NULL,
  model       TEXT NOT NULL,
  verdict     TEXT NOT NULL,      -- 'pass' | 'honeypot'
  attested_at TEXT NOT NULL,      -- ISO8601 UTC (the timestamp carried with the row)
  attester    TEXT,               -- attester IP / id -> invalidate by this
  source      TEXT
);
CREATE INDEX IF NOT EXISTS hosts_attested_hm ON hosts_attested (host, model);
CREATE INDEX IF NOT EXISTS hosts_attested_by ON hosts_attested (attester);

DROP VIEW IF EXISTS host_resolved;
CREATE VIEW host_resolved AS
WITH u AS (
  SELECT host, model, verdict, ts          AS ts, 'measured' AS provenance, NULL AS attester FROM host_measured
  UNION ALL
  SELECT host, model, verdict, attested_at AS ts, 'attested' AS provenance, attester        FROM hosts_attested
),
ranked AS (
  SELECT host, model, verdict, ts, provenance, attester,
         ROW_NUMBER() OVER (
           PARTITION BY host, model
           ORDER BY (verdict = 'honeypot') DESC,                             -- honeypot wins (one-way, trusted)
                    (verdict = 'honeypot' AND provenance = 'measured') DESC,  -- own burn outranks peer's (provenance)
                    ts DESC                                                   -- else most-recent wins (fill-blanks)
         ) AS rn
  FROM u
)
SELECT host, model, verdict, provenance, ts, attester
FROM ranked WHERE rn = 1;
