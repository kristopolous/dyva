<?php
// gossiper.php — dyva gossiper server (PHP/Apache deployment, e.g. 9ol.es/dyva/gossiper).
//
// Peer sync of SMOKE-TEST verdicts between dyva instances. Design + rationale:
// brainstorm/gossiper.md. Portable zero-dependency twin: gossiper.py (identical contract).
//
// CONTRACT
//   GET|POST  ...?source=<sha1 hex>&since=<int cursor>
//     POST body (JSON): {"since": <int>, "rows": [{"host","model","smoke","ts"}, ...]}
//       - POST ingests `rows` (GET ingests nothing); both return the delta.
//     -> {"ok":true, "now":<cursor to persist>, "rows":[ rows for this source with id>since ]}
//
//   `source` = sha1 of the source URL. It is the ISOLATION key: a client only ever sees or
//   writes rows for a source it holds the URL of (hence can hash). A non-sha1 `source` is a
//   400. This is why sync exposure never exceeds source exposure (see the doc).
//
//   The delta cursor is the autoincrement `id` (monotonic, immune to clock skew and to the
//   "repeats in waves" a windowed LIMIT causes). The client persists `now` and sends it back
//   as `since`; `now` = the max id actually RETURNED (never the table max), so concurrent
//   inserts are picked up next sync instead of being skipped. The client's own just-posted
//   rows echo back once (this sync only); fill-blanks makes that a harmless no-op.
//
//   Store is UNBOUNDED, append-only. No row cap / recency window (deliberately — see doc).
//   `src_ip` is recorded per row so a bad contributor can be invalidated later.
//
// DEPLOY
//   Route /dyva/gossiper to this file (DirectoryIndex or a RewriteRule). The sqlite db is
//   written next to this file by default; the sibling .htaccess DENIES downloading *.sqlite*
//   and the .py source. Point GOSSIPER_DB at a path OUTSIDE the docroot if you'd rather.
//   The web-server user must be able to write the db file and its directory (WAL needs both).

header('Content-Type: application/json');

$DB = getenv('GOSSIPER_DB') ?: (__DIR__ . '/gossiper.sqlite');
$MAX_BODY = 8 * 1024 * 1024;   // defensive request cap (NOT a store cap)

function fail(int $code, string $msg): void {
    http_response_code($code);
    echo json_encode(['ok' => false, 'error' => $msg]);
    exit;
}

$source = isset($_GET['source']) ? (string) $_GET['source'] : '';
if (!preg_match('/^[0-9a-f]{40}$/i', $source)) {
    fail(400, 'source must be a sha1 hex (the isolation key)');
}
$since = isset($_GET['since']) ? (int) $_GET['since'] : 0;

try {
    $db = new PDO('sqlite:' . $DB);
    $db->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
    $db->exec('PRAGMA journal_mode=WAL');
    $db->exec('PRAGMA busy_timeout=5000');
    $db->exec(
        'CREATE TABLE IF NOT EXISTS gossip (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            source    TEXT NOT NULL,
            host      TEXT NOT NULL,
            model     TEXT,
            smoke     TEXT,
            client_ts TEXT,
            src_ip    TEXT,
            server_ts TEXT DEFAULT (strftime(\'%Y-%m-%dT%H:%M:%fZ\', \'now\'))
        )'
    );
    $db->exec('CREATE INDEX IF NOT EXISTS gossip_src_id ON gossip (source, id)');
} catch (Throwable $e) {
    fail(500, 'db open failed');
}

// Best-effort client IP (we sit behind Apache / a proxy), recorded for retro-invalidation.
$ip = $_SERVER['REMOTE_ADDR'] ?? '';
if (!empty($_SERVER['HTTP_X_FORWARDED_FOR'])) {
    $ip = trim(explode(',', $_SERVER['HTTP_X_FORWARDED_FOR'])[0]);
}

if (($_SERVER['REQUEST_METHOD'] ?? 'GET') === 'POST') {
    $raw = file_get_contents('php://input', false, null, 0, $MAX_BODY + 1);
    if ($raw !== false && strlen($raw) > $MAX_BODY) {
        fail(413, 'payload too large');
    }
    $body = json_decode((string) $raw, true);
    if (is_array($body)) {
        if (isset($body['since'])) {
            $since = (int) $body['since'];
        }
        $rows = (isset($body['rows']) && is_array($body['rows'])) ? $body['rows'] : [];
        if ($rows) {
            $ins = $db->prepare(
                'INSERT INTO gossip (source, host, model, smoke, client_ts, src_ip)
                 VALUES (?, ?, ?, ?, ?, ?)'
            );
            $db->beginTransaction();
            foreach ($rows as $r) {
                if (!is_array($r) || empty($r['host'])) {
                    continue;   // host is required; skip junk rather than fail the batch
                }
                $ins->execute([
                    $source,
                    (string) $r['host'],
                    isset($r['model']) ? (string) $r['model'] : null,
                    isset($r['smoke']) ? (string) $r['smoke'] : null,
                    isset($r['ts'])    ? (string) $r['ts']    : null,
                    $ip,
                ]);
            }
            $db->commit();
        }
    }
}

// Delta back: every row for THIS source newer than the client's cursor.
$sel = $db->prepare(
    'SELECT id, host, model, smoke, client_ts, server_ts
     FROM gossip WHERE source = ? AND id > ? ORDER BY id ASC'
);
$sel->execute([$source, $since]);
$out = $sel->fetchAll(PDO::FETCH_ASSOC);

// Cursor to persist / send next time = max id ACTUALLY returned (not the table max), so a
// concurrent insert can never be skipped.
$now = $since;
foreach ($out as &$r) {
    $r['id'] = (int) $r['id'];
    if ($r['id'] > $now) {
        $now = $r['id'];
    }
}
unset($r);

echo json_encode(['ok' => true, 'now' => $now, 'rows' => $out]);
