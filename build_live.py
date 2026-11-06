import io
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(BASE_DIR, 'live.txt')

TARGET_GROUP = '江苏地方'
SOURCE_GROUP = '组播线路'
DROP_GROUPS = ('河南地方', '电影在线', '影音剧场')
DROP_CHANNEL_PREFIX = '河南'
MIN_CHANNELS = 10  # 江苏地方至少要有这么多台，否则视为上游异常，不覆盖文件

# (组播线路里的原始频道名, 江苏地方分组里的名称)
MAPPING = [
    ('江苏卫视', '江苏卫视'),
    ('江苏城市频道', '江苏城市'),
    ('江苏综艺频道', '江苏综艺'),
    ('江苏影视频道', '江苏影视'),
    ('公共新闻频道', '江苏公共新闻'),
    ('江苏教育', '江苏教育'),
    ('体育休闲频道', '江苏体育休闲'),
    ('优漫卡通频道', '优漫卡通'),
    ('江苏国际', '江苏国际'),
    ('南京新闻综合频道', '南京新闻综合'),
    ('南京十八频道', '南京十八'),
    ('南京教科频道', '南京教科'),
    ('徐州新闻综合', '徐州新闻综合'),
    ('南通新闻综合', '南通新闻综合'),
    ('盐城新闻综合', '盐城新闻综合'),
    ('淮安新闻综合', '淮安新闻综合'),
    ('泰州新闻综合', '泰州新闻综合'),
    ('连云港新闻综合', '连云港新闻综合'),
    ('宿迁新闻综合', '宿迁新闻综合'),
    ('宜兴新闻综合', '宜兴新闻综合'),
    ('溧水新闻综合', '溧水新闻综合'),
]


def read_lines(path):
    if not os.path.exists(path):
        return []
    with io.open(path, 'r', encoding='utf-8-sig', newline='') as f:
        raw = f.read()
    return [l for l in raw.replace('\r\n', '\n').split('\n')]


def drop_group(lines, keyword):
    """删除分组名包含 keyword 的整个分组（分组头 + 组内频道）。"""
    out, i, dropped = [], 0, 0
    while i < len(lines):
        if '#genre#' in lines[i] and keyword in lines[i]:
            i += 1
            while i < len(lines) and '#genre#' not in lines[i]:
                i += 1
                dropped += 1
            continue
        out.append(lines[i])
        i += 1
    return out, dropped


def group_range(lines, keyword):
    """返回分组内频道的行号区间 [start, end)，找不到返回 (0, 0)。"""
    start = end = 0
    for idx, line in enumerate(lines):
        if '#genre#' in line:
            if start and not end:
                end = idx
            if keyword in line:
                start = idx + 1
    if start and not end:
        end = len(lines)
    return (start, end) if start else (0, 0)


def build(base_lines, fallback=None):
    """对 base_lines 应用规则，返回 (新行列表, 统计信息)。"""
    fallback = fallback or {}
    lines = [l for l in base_lines if l.strip()]

    # 1. 删除旧江苏地方分组（其链接留作兜底），再删除指定分组与河南频道
    start, end = group_range(lines, TARGET_GROUP)
    old = {}
    for line in lines[start:end]:
        if ',' in line:
            name, url = line.split(',', 1)
            old[name.strip()] = url
    old.update(fallback)
    lines, n_target = drop_group(lines, TARGET_GROUP)

    dropped = {}
    for keyword in DROP_GROUPS:
        lines, n = drop_group(lines, keyword)
        if n:
            dropped[keyword] = n
    henan = [l for l in lines if l.startswith(DROP_CHANNEL_PREFIX)]
    lines = [l for l in lines if not l.startswith(DROP_CHANNEL_PREFIX)]

    # 2. 从组播线路取江苏频道（取到后从原分组移走，避免同一个台出现两次）
    start, end = group_range(lines, SOURCE_GROUP)
    found = {}
    for line in lines[start:end]:
        if ',' not in line:
            continue
        name, url = line.split(',', 1)
        name = name.strip()
        for src_name, _ in MAPPING:
            if name == src_name and src_name not in found:
                found[src_name] = url
                break

    group, missing = ['🇨🇳%s,#genre#' % TARGET_GROUP], []
    for src_name, new_name in MAPPING:
        url = found.get(src_name) or old.get(new_name)
        if url:
            group.append(new_name + ',' + url)
        else:
            missing.append(new_name)

    moved = set('%s,%s' % (k, v) for k, v in found.items())
    lines = [l for l in lines if l not in moved]

    stats = {
        'old_target': n_target,
        'dropped': dropped,
        'henan': len(henan),
        'channels': len(group) - 1,
        'missing': missing,
    }
    return group + lines, stats


def main():
    local_lines = read_lines(PATH)
    if not local_lines:
        raise SystemExit('未找到 live.txt')

    fallback = {}
    start, end = group_range(local_lines, TARGET_GROUP)
    for line in local_lines[start:end]:
        if ',' in line:
            name, url = line.split(',', 1)
            fallback[name.strip()] = url

    out, stats = build(local_lines, fallback)

    # 校验：结构完整才覆盖，避免把订阅写坏
    genre_count = sum(1 for l in out if '#genre#' in l)
    if stats['channels'] < MIN_CHANNELS:
        raise SystemExit('江苏地方只有 %s 个频道（少于 %s），放弃写入' % (stats['channels'], MIN_CHANNELS))
    if genre_count < 3:
        raise SystemExit('分组数量异常（%s），放弃写入' % genre_count)
    if any('河南' in l for l in out):
        raise SystemExit('仍有河南频道残留，放弃写入')

    with io.open(PATH, 'w', encoding='utf-8', newline='') as f:
        f.write('\r\n'.join(out) + '\r\n')

    print('删除旧江苏地方分组: %s 行' % stats['old_target'])
    print('删除分组: %s' % ('、'.join('%s(%s行)' % (k, v) for k, v in stats['dropped'].items()) or '无'))
    print('其它河南频道: %s 行' % stats['henan'])
    print('江苏地方: %s 个频道' % stats['channels'])
    if stats['missing']:
        print('缺少可用源: %s' % ', '.join(stats['missing']))
    print('已写入 %s（%s 行, %s 个分组）' % (PATH, len(out), genre_count))


if __name__ == '__main__':
    sys.exit(main())
