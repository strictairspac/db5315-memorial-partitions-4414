from collections import Counter
from dataclasses import dataclass
from datetime import date
from uuid import uuid4

from sqlalchemy import text


REGISTERED = '在册'
PENDING = '待挂载'
DEFAULT = 'DEFAULT 待核池'
ONLINE = '在线账簿'
ARCHIVE = '摘除表'
ABSENT = '不在本次账簿'

SPEC = {
    'name': '松门纪念园·入园凭条分区',
    'summary': '核对凭条分流、漏建月份回补和整月到期摘除，确认每张凭条去向。',
    'customer': '松门纪念园·票务台账组',
    'entity_labels': ['凭条账簿', '生命周期执行', '回收核验'],
    'field_labels': {
        'cutoff': '保留截止日',
        'partitions': '月分区边界',
        'month': '月初日期',
        'state': '分区登记状态',
        'records': '入园凭条',
        'slip_id': '凭条编号',
        'entered_on': '入园日期',
    },
    'guidance': '日期填 YYYY-MM-DD。分区填月初，状态填「在册」或「待挂载」。次月月初等于或早于截止日即整月到期。调整截止日先看方案预览，再确认生效。',
    'sample': {
        'cutoff': '2026-09-01',
        'partitions': [
            {'month': '2026-07-01', 'state': PENDING},
            {'month': '2026-08-01', 'state': REGISTERED},
            {'month': '2026-09-01', 'state': PENDING},
            {'month': '2026-10-01', 'state': REGISTERED},
        ],
        'records': [
            {'slip_id': 'SM-0731-01', 'entered_on': '2026-07-31'},
            {'slip_id': 'SM-0801-01', 'entered_on': '2026-08-01'},
            {'slip_id': 'SM-0831-01', 'entered_on': '2026-08-31'},
            {'slip_id': 'SM-0901-01', 'entered_on': '2026-09-01'},
            {'slip_id': 'SM-0930-01', 'entered_on': '2026-09-30'},
            {'slip_id': 'SM-1001-01', 'entered_on': '2026-10-01'},
            {'slip_id': 'SM-1031-01', 'entered_on': '2026-10-31'},
            {'slip_id': 'SM-1101-01', 'entered_on': '2026-11-01'},
        ],
    },
    'expect': {
        '初始待核凭条': 4, '回补凭条': 2, '挂载月份': 1, '摘除月份': 1,
        '摘除凭条': 2, '在线凭条': 6, '剩余待核凭条': 2, '截止日前待核凭条': 1,
    },
}


@dataclass(frozen=True)
class Month:
    start: date
    end: date
    state: str

    @property
    def label(self):
        return self.start.isoformat()[:7]

    @property
    def sql_bounds(self):
        # These literals come from validated date objects. PostgreSQL DDL requires constants.
        return f"FOR VALUES FROM (DATE '{self.start.isoformat()}') TO (DATE '{self.end.isoformat()}')"


def _read_date(value, label):
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError(f'{label}须为有效日期（YYYY-MM-DD）') from None
    if parsed.isoformat() != value:
        raise ValueError(f'{label}须为 YYYY-MM-DD 格式')
    return parsed


def _read_rows(payload, key):
    rows = payload.get(key)
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError(f'{SPEC["field_labels"][key]}须为对象数组')
    return rows


def _parse(payload):
    if not isinstance(payload, dict):
        raise ValueError('凭条账簿须为对象')
    cutoff = _read_date(payload.get('cutoff'), '保留截止日')
    months = {}
    for index, row in enumerate(_read_rows(payload, 'partitions'), 1):
        start = _read_date(row.get('month'), f'第 {index} 行月初日期')
        state = row.get('state')
        if start.day != 1 or start == date(9999, 12, 1):
            raise ValueError(f'分区 {start} 须从月初起算，且须有可表示的次月月初')
        if start in months:
            raise ValueError(f'分区月份 {start:%Y-%m} 重复')
        if state not in (REGISTERED, PENDING):
            raise ValueError(f'分区 {start:%Y-%m} 状态须为「在册」或「待挂载」')
        end = date(start.year + start.month // 12, start.month % 12 + 1, 1)
        months[start] = Month(start, end, state)

    slips = {}
    for index, row in enumerate(_read_rows(payload, 'records'), 1):
        slip_id = row.get('slip_id')
        if not isinstance(slip_id, str) or not slip_id or slip_id != slip_id.strip() or '\x00' in slip_id:
            raise ValueError(f'第 {index} 行凭条编号须为非空文本，且不能含空字符或首尾空白')
        if slip_id in slips:
            raise ValueError(f'凭条编号 {slip_id} 重复')
        slips[slip_id] = _read_date(row.get('entered_on'), f'凭条 {slip_id} 入园日期')
    return cutoff, sorted(months.values(), key=lambda month: month.start), slips


def validate(payload):
    _parse(payload)


def _decide(months, cutoff):
    """Derive the mount/detach plan from validated input. Pure, side-effect free."""
    registered = {month.start for month in months if month.state == REGISTERED}
    detached = {month.start for month in months if month.state == REGISTERED and month.end <= cutoff}
    attached = {month.start for month in months if month.state == PENDING and month.end > cutoff}
    mounted = registered | attached
    online = mounted - detached
    return registered, attached, detached, mounted, online


def _route_slips(months, cutoff, slips):
    """Compute every slip's destination without touching PostgreSQL."""
    registered, attached, detached, mounted, online = _decide(months, cutoff)
    by_start = {month.start: month for month in months}
    rows = []
    for slip_id in sorted(slips, key=lambda item: (slips[item], item)):
        entered_on = slips[slip_id]
        start = entered_on.replace(day=1)
        month = by_start.get(start)
        initial_label = month.label if month and month.start in registered else DEFAULT
        if start in mounted:
            label = month.label
            location = ARCHIVE if start in detached else ONLINE
        else:
            label, location = DEFAULT, DEFAULT
        if location == ARCHIVE:
            disposition = '摘除表已核实'
        elif location == DEFAULT:
            disposition = '截止日前待复核' if entered_on < cutoff else '等待挂载'
        elif initial_label == DEFAULT:
            disposition = '已从待核池回补'
        else:
            disposition = '在线留存'
        rows.append({
            '凭条编号': slip_id, '入园日期': entered_on.isoformat(),
            '初始去向': initial_label, '去向': label, '位置': location, '核验结果': disposition,
        })
    return rows


def _partition_rows(months, cutoff, slip_rows):
    registered, attached, detached, mounted, online = _decide(months, cutoff)
    rows = []
    for month in months:
        start = month.start
        if start in detached:
            action, destination = '整月到期，将摘除', ARCHIVE
        elif start in attached:
            action, destination = '仍在保留期，将挂载回补', ONLINE
        elif month.state == PENDING:
            action, destination = '整月到期，略过挂载', None
        else:
            action, destination = '仍在保留期，继续挂载', ONLINE
        counts = Counter(row['位置'] for row in slip_rows if row['去向'] == month.label)
        rows.append({
            '分区月份': month.label, '下界（含）': start.isoformat(), '上界（不含）': month.end.isoformat(),
            '登记状态': month.state, '计划处置': action, '计划位置': destination or '未建立',
            '在线张数': counts[ONLINE], '摘除张数': counts[ARCHIVE], '待核张数': counts[DEFAULT],
            '分区张数': sum(counts.values()),
        })
    return rows


def preview(payload, baseline=None):
    """Describe the impact of a cutoff plan before it is applied.

    ``baseline`` is the structured state of the latest committed evidence, or None.
    """
    cutoff, months, slips = _parse(payload)
    slip_rows = _route_slips(months, cutoff, slips)
    partition_rows = _partition_rows(months, cutoff, slip_rows)
    old_slips = (baseline or {}).get('slips') or {}
    old_parts = (baseline or {}).get('partitions') or {}

    rows = []
    for row in slip_rows:
        previous = old_slips.get(row['凭条编号'])
        old_label = previous['分区'] if previous else '首次入账'
        old_location = previous['位置'] if previous else '首次入账'
        changed = old_label != row['去向'] or old_location != row['位置']
        rows.append({
            '凭条编号': row['凭条编号'], '入园日期': row['入园日期'],
            '原去向': old_label, '原位置': old_location,
            '新去向': row['去向'], '新位置': row['位置'], '变化': '是' if changed else '否',
            '核验结果': row['核验结果'],
        })
    known = {row['凭条编号'] for row in slip_rows}
    for slip_id, previous in sorted(old_slips.items()):
        if slip_id in known:
            continue
        rows.append({
            '凭条编号': slip_id, '入园日期': previous['入园日期'],
            '原去向': previous['分区'], '原位置': previous['位置'],
            '新去向': ABSENT, '新位置': '未入账', '变化': '是', '核验结果': '本次账簿未包含，将移出',
        })
    rows.sort(key=lambda row: (row['入园日期'], row['凭条编号']))

    partitions = []
    for row in partition_rows:
        previous = old_parts.get(row['分区月份']) or {}
        old_location = previous.get('位置', '未建立')
        changed = old_location != row['计划位置']
        partitions.append({**row, '原位置': old_location, '变化': '是' if changed else '否'})
    for label in sorted(old_parts):
        if any(row['分区月份'] == label for row in partitions):
            continue
        previous = old_parts[label]
        partitions.append({
            '分区月份': label, '下界（含）': previous.get('下界', ''), '上界（不含）': previous.get('上界', ''),
            '登记状态': previous.get('登记状态', ''), '计划处置': '本次账簿未包含，将移出分区目录',
            '计划位置': '未建立', '在线张数': 0, '摘除张数': 0, '待核张数': 0, '分区张数': 0,
            '原位置': previous.get('位置', '未建立'), '变化': '是',
        })

    online_rows = [row for row in rows if row['新位置'] == ONLINE or row['原位置'] == ONLINE]
    archive_rows = [row for row in rows if row['新位置'] == ARCHIVE or row['原位置'] == ARCHIVE]
    counts = Counter(row['新位置'] for row in rows)
    restored = sum(row['原位置'] == ARCHIVE and row['新位置'] == ONLINE for row in rows)
    newly_archived = sum(row['原位置'] != ARCHIVE and row['新位置'] == ARCHIVE for row in rows)
    removed = sum(row['原位置'] in (ONLINE, ARCHIVE) and row['新位置'] == '未入账' for row in rows)
    unchanged = sum(row['变化'] == '否' for row in rows)
    added = sum(row['原位置'] == '首次入账' for row in rows)
    return {
        'cutoff': cutoff.isoformat(),
        'partitions': partitions,
        'slips': rows,
        'online': online_rows,
        'archived': archive_rows,
        'summary': {
            '分区目录': len(partitions), '分区目录变化': sum(row['变化'] == '是' for row in partitions),
            '在线凭条': counts[ONLINE], '摘除凭条': counts[ARCHIVE], '待核凭条': counts[DEFAULT],
            '新入账': added, '移出账簿': removed, '新增摘除': newly_archived,
            '摘除后回到在线': restored, '去向不变': unchanged,
        },
    }


def _read_slips(conn, tables):
    observed = {}
    for table in tables:
        rows = conn.execute(text(f'''
            SELECT s.slip_id, s.entered_on, c.relname
            FROM pg_temp.{table} AS s
            JOIN pg_catalog.pg_class AS c ON c.oid = s.tableoid
        '''))
        for slip_id, entered_on, leaf in rows:
            if slip_id in observed:
                raise RuntimeError(f'分区核对失败，凭条 {slip_id} 出现多次')
            observed[slip_id] = (entered_on, leaf)
    return observed


def _verify_slips(observed, slips, month_tables, default, phase):
    if observed.keys() != slips.keys():
        raise RuntimeError(f'{phase}核对失败，数据库凭条编号与输入不一致')
    for slip_id, entered_on in slips.items():
        expected_leaf = month_tables.get(entered_on.replace(day=1), default)
        if observed[slip_id] != (entered_on, expected_leaf):
            raise RuntimeError(f'{phase}核对失败，凭条 {slip_id} 的日期或分区不一致')


def _read_bounds(conn, parent):
    return dict(conn.execute(text('''
        SELECT child.relname, pg_catalog.pg_get_expr(child.relpartbound, child.oid)
        FROM pg_catalog.pg_inherits AS edge
        JOIN pg_catalog.pg_class AS child ON child.oid = edge.inhrelid
        WHERE edge.inhparent = pg_catalog.to_regclass(:parent)
    '''), {'parent': f'pg_temp.{parent}'}).tuples().all())


def _backfill(conn, parent, default, table, month):
    conn.execute(text(f'''
        CREATE TEMP TABLE pg_temp.{table} (
            LIKE pg_temp.{parent},
            CHECK (entered_on >= DATE '{month.start.isoformat()}' AND entered_on < DATE '{month.end.isoformat()}')
        ) ON COMMIT DROP
    '''))
    conn.execute(text(f'''
        WITH moved AS (
            DELETE FROM ONLY pg_temp.{default}
            WHERE entered_on >= :lower AND entered_on < :upper
            RETURNING slip_id, entered_on
        )
        INSERT INTO pg_temp.{table} (slip_id, entered_on)
        SELECT slip_id, entered_on FROM moved
    '''), {'lower': month.start, 'upper': month.end})
    conn.execute(text(f'ALTER TABLE pg_temp.{parent} ATTACH PARTITION pg_temp.{table} {month.sql_bounds}'))


def solve_in(conn, payload, baseline=None):
    """Mount, detach and reconcile in one transaction on ``conn``.

    The caller owns the transaction. Any exception leaves the previous committed
    state untouched and makes PostgreSQL roll the temporary objects away.
    """
    cutoff, months, slips = _parse(payload)
    parent = f'gb5315_{uuid4().hex}'
    default = f'{parent}_default'
    tables = {month.start: f'{parent}_m{month.label.replace("-", "")}' for month in months}
    registered, attached, detached, mounted, online = _decide(months, cutoff)
    registered_tables = {month.start: tables[month.start] for month in months if month.start in registered}
    attached_tables = {month.start: tables[month.start] for month in months if month.start in attached}
    detached_tables = {month.start: tables[month.start] for month in months if month.start in detached}
    mounted_tables = registered_tables | attached_tables
    online_tables = {start: table for start, table in mounted_tables.items() if start not in detached}

    conn.execute(text("SET LOCAL DateStyle = 'ISO, YMD'"))
    conn.execute(text(f'''
        CREATE TEMP TABLE pg_temp.{parent} (
            slip_id text NOT NULL, entered_on date NOT NULL
        ) PARTITION BY RANGE (entered_on) ON COMMIT DROP
    '''))
    for month in months:
        if month.start in registered:
            table = registered_tables[month.start]
            conn.execute(text(f'''
                CREATE TEMP TABLE pg_temp.{table} PARTITION OF pg_temp.{parent}
                {month.sql_bounds} ON COMMIT DROP
            '''))
    conn.execute(text(f'''
        CREATE TEMP TABLE pg_temp.{default} PARTITION OF pg_temp.{parent} DEFAULT ON COMMIT DROP
    '''))
    if slips:
        rows = [{'slip_id': slip_id, 'entered_on': entered_on} for slip_id, entered_on in slips.items()]
        conn.execute(text(f'''
            INSERT INTO pg_temp.{parent} (slip_id, entered_on) VALUES (:slip_id, :entered_on)
        '''), rows)
    initial = _read_slips(conn, [parent])
    _verify_slips(initial, slips, registered_tables, default, '初始分流')

    for month in months:
        if month.start in attached:
            _backfill(conn, parent, default, attached_tables[month.start], month)
    filled = _read_slips(conn, [parent])
    _verify_slips(filled, slips, mounted_tables, default, '挂载回补')
    mounted_bounds = _read_bounds(conn, parent)
    if mounted_bounds.keys() != {default, *mounted_tables.values()}:
        raise RuntimeError('挂载核对失败，PostgreSQL 分区目录与计划不一致')

    for table in detached_tables.values():
        conn.execute(text(f'ALTER TABLE pg_temp.{parent} DETACH PARTITION pg_temp.{table}'))
    remaining = _read_slips(conn, [parent])
    archived = _read_slips(conn, detached_tables.values())
    if remaining.keys() & archived.keys():
        raise RuntimeError('摘除核对失败，凭条同时存在于在线账簿和摘除表')
    final = remaining | archived
    _verify_slips(final, slips, mounted_tables, default, '摘除后对账')
    final_bounds = _read_bounds(conn, parent)
    if final_bounds.keys() != {default, *online_tables.values()}:
        raise RuntimeError('摘除核对失败，PostgreSQL 分区目录与到期计划不一致')
    if any(leaf not in final_bounds for _, leaf in remaining.values()):
        raise RuntimeError('摘除核对失败，在线账簿与 PostgreSQL 分区目录不一致')
    if any(leaf in final_bounds for _, leaf in archived.values()):
        raise RuntimeError('摘除核对失败，摘除表仍在 PostgreSQL 分区目录中')

    labels = {tables[month.start]: month.label for month in months}
    labels[default] = DEFAULT
    initial_counts = Counter(leaf for _, leaf in initial.values())
    filled_counts = Counter(leaf for _, leaf in filled.values())
    online_counts = Counter(leaf for _, leaf in remaining.values())
    archive_counts = Counter(leaf for _, leaf in archived.values())
    month_rows = []
    for month in months:
        table = tables[month.start]
        if month.start in detached:
            decision = '整月到期，已摘除'
        elif month.start in attached:
            decision = '仍在保留期，已挂载'
        elif month.state == PENDING:
            decision = '整月到期，略过挂载'
        else:
            decision = '仍在保留期，继续挂载'
        month_rows.append({
            '分区月份': month.label, '下界（含）': month.start.isoformat(), '上界（不含）': month.end.isoformat(),
            '登记状态': month.state, '处置': decision,
            '挂载后数据库边界': mounted_bounds.get(table, '未挂载'),
            '摘除后仍在分区目录': '是' if table in final_bounds else '否',
            '初始张数': initial_counts[table], '回补后张数': filled_counts[table],
            '在线张数': online_counts[table], '摘除张数': archive_counts[table],
        })
    slip_rows = []
    for slip_id, entered_on in sorted(slips.items(), key=lambda item: (item[1], item[0])):
        leaf = final[slip_id][1]
        if slip_id in archived:
            disposition = '摘除表已核实'
        elif leaf == default:
            disposition = '截止日前待复核' if entered_on < cutoff else '等待挂载'
        elif initial[slip_id][1] == default:
            disposition = '已从待核池回补'
        else:
            disposition = '在线留存'
        slip_rows.append({
            '凭条编号': slip_id, '入园日期': entered_on.isoformat(),
            '初始路由': labels[initial[slip_id][1]], '挂载后路由': labels[filled[slip_id][1]],
            '最终存放': labels[leaf], '核验结果': disposition,
        })
    moved = initial_counts[default] - filled_counts[default]
    overdue = sum(leaf == default and day < cutoff for day, leaf in remaining.values())
    report = {
        'summary': f'按 {cutoff} 核验 {len(slips)} 张凭条，回补 {moved} 张，摘除 {len(archived)} 张，待复核 {online_counts[default]} 张。',
        'metrics': {
            '收到凭条': len(slips), '初始待核凭条': initial_counts[default], '回补凭条': moved,
            '挂载月份': len(attached), '摘除月份': len(detached), '摘除凭条': len(archived),
            '在线凭条': len(remaining), '剩余待核凭条': online_counts[default],
            '截止日前待核凭条': overdue,
        },
        'sections': [
            {'title': '保留期对账', 'rows': [
                {'时点': '初始分流', '在线张数': len(initial), '其中待核': initial_counts[default], '摘除张数': 0},
                {'时点': '挂载回补后', '在线张数': len(filled), '其中待核': filled_counts[default], '摘除张数': 0},
                {'时点': '摘除核验后', '在线张数': len(remaining), '其中待核': online_counts[default], '摘除张数': len(archived)},
            ]},
            {'title': '月分区处置', 'rows': month_rows},
            {'title': '逐张凭条去向', 'rows': slip_rows},
        ],
        'state': {
            'cutoff': cutoff.isoformat(),
            'partitions': {
                month.label: {
                    '登记状态': month.state,
                    '位置': ONLINE if month.start in online else (ARCHIVE if month.start in detached else '未建立'),
                    '下界': month.start.isoformat(), '上界': month.end.isoformat(),
                    '边界': mounted_bounds.get(tables[month.start], '未挂载'),
                }
                for month in months
            },
            'slips': {
                slip_id: {
                    '入园日期': entered_on.isoformat(),
                    '分区': labels[final[slip_id][1]],
                    '位置': ARCHIVE if slip_id in archived else (DEFAULT if final[slip_id][1] == default else ONLINE),
                }
                for slip_id, entered_on in slips.items()
            },
        },
    }
    _attach_comparison(report, baseline)
    return report


def _attach_comparison(report, baseline):
    """Add old/new destinations to an applied report, relative to the baseline state."""
    old_slips = (baseline or {}).get('slips') or {}
    rows = report['sections'][2]['rows']
    for row in rows:
        previous = old_slips.get(row['凭条编号'])
        row['原去向'] = previous['分区'] if previous else '首次入账'
        row['原位置'] = previous['位置'] if previous else '首次入账'
        row['新去向'] = row['最终存放']
        row['新位置'] = report['state']['slips'][row['凭条编号']]['位置']
        row['变化'] = '是' if (row['原去向'], row['原位置']) != (row['新去向'], row['新位置']) else '否'
    known = {row['凭条编号'] for row in rows}
    for slip_id, previous in sorted(old_slips.items()):
        if slip_id in known:
            continue
        rows.append({
            '凭条编号': slip_id, '入园日期': previous['入园日期'],
            '初始路由': '—', '挂载后路由': '—', '最终存放': ABSENT,
            '核验结果': '本次账簿未包含，已移出', '原去向': previous['分区'], '原位置': previous['位置'],
            '新去向': ABSENT, '新位置': '未入账', '变化': '是',
        })
    rows.sort(key=lambda row: (row['入园日期'], row['凭条编号']))
    changed = sum(row['变化'] == '是' for row in rows)
    report['metrics']['去向变化'] = changed
    report['metrics']['去向不变'] = len(rows) - changed
    if old_slips:
        report['metrics']['摘除后回到在线'] = sum(
            row['原位置'] == ARCHIVE and row['新位置'] == ONLINE for row in rows)
        report['metrics']['本次新摘除'] = sum(
            row['原位置'] != ARCHIVE and row['新位置'] == ARCHIVE for row in rows)
        report['metrics']['移出账簿'] = sum(
            row['原位置'] in (ONLINE, ARCHIVE) and row['新位置'] == '未入账' for row in rows)


def solve(payload, engine):
    with engine.begin() as conn:
        return solve_in(conn, payload)
