"""Row-level read and write access to the schema, sitting above darts.db.

Two rules hold across every module here, and the rest of the layer follows from
them.

**The caller owns the transaction.** No function in this package opens one.
They take a `sqlite3.Connection` and issue statements on it; whoever wants a
write to be atomic wraps the call in `darts.db.connection.transaction`. This is
not a style preference: `transaction()` refuses to nest, so if each repository
function owned a transaction then `create_match` could not call four of them.
Pushing the boundary up one level is what makes the repositories composable.

    with transaction(conn):
        created = create_match(conn, config, teams)

`create_match` is the one function that insists on it, because "match, teams,
members and leg 0 in a single transaction" is a promise it cannot keep alone;
it raises if the caller has not begun one.

**A match's configuration is validated once.** `GameConfig` is the only way to
describe a match, it mirrors the schema's own CHECK constraints, and
`create_match` writes it to `config_json` and to the promoted columns from that
one validated object. The two cannot disagree because nothing else can write
them.
"""
