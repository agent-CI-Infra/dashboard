"""Append to workspace/archive helpers; open only private DB+WAL copies."""

HIVE_DB_BYTES = 512 * 1024 * 1024

@contextlib.contextmanager
def hive_snapshot(db):
    for _ in range(3):
        before = archive_signature(db)
        if not before[0] or before[3]:
            raise ValueError('missing DB or rollback journal')
        if sum(s[2] for s in before[:2] if s is not None) > HIVE_DB_BYTES:
            raise ValueError('image exceeds bound')
        with tempfile.TemporaryDirectory(prefix='sfs-hive-') as folder:
            local = Path(folder) / 'copy.sqlite3'
            if local.resolve().is_relative_to(Path('/mnt/sfs')) or local.resolve().is_relative_to(ROOT.resolve()):
                raise ValueError('temporary directory cannot be on SFS')
            if copy_archive_image(db, local, before) is None:
                continue
            yield local
            return
    raise ValueError('source kept changing during copy')


if __name__ == '__main__':
    command = json.load(sys.stdin)
    json.dump(collect_hive_daily(command['names'], path_for, hive_snapshot), sys.stdout, ensure_ascii=False)
