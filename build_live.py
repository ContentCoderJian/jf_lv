"""基于作者订阅地址生成 live.txt。

规则：
  1. 拉取作者最新 live.txt（失败则退回仓库里现有的 live.txt）；
  2. 分组名去掉国旗/表情前缀（🇨🇳 等）；
  3. 删除河南地方、电影在线、影音剧场三个分组，以及所有河南频道；
  4. 江苏本地台单独作为「江苏地方」分组放到文件最前面；
  5. 仅对 IPV4线路 分组内的 CCTV 频道做处理：名字去掉横线和中文后缀（CCTV-1 → CCTV1、CCTV1综合 → CCTV1），
     并按台号（CCTV1、CCTV2、CCTV3…）排在 IPV4线路 分组最前面；组播线路保持原样；
  6. 校验通过后覆盖写入 live.txt。

用法：
  python build_live.py            # 先拉作者地址，拉不到用本地
  python build_live.py --offline  # 不联网，只用本地 live.txt
"""
import io
import os
import re
import sys
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(BASE_DIR, 'live.txt')

# 作者订阅地址（ghfast 为国内加速镜像，失败再走 GitHub 原始地址）
UPSTREAM_URLS = [
    'https://ghfast.top/raw.githubusercontent.com/Supprise0901/TVBox_live/main/live.txt',
    'https://raw.githubusercontent.com/Supprise0901/TVBox_live/main/live.txt',
]

TARGET_GROUP = '江苏地方'      # 江苏本地台分组（放最前）
IPV4_GROUP = 'IPV4线路'        # 需要整理 CCTV 的分组
SOURCE_GROUP = '组播线路'      # 江苏频道从这里取源
LEGACY_CCTV_GROUP = '央视频道'  # 早期版本生成过的分组，若存在则并回 IPV4线路
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

# 分组名前面的国旗/表情前缀，如 🇨🇳、🛰️
GROUP_PREFIX = re.compile(r'^[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200d\s]+')


CCTV_PREFIX = re.compile(r'^CCTV\s*[\-－]?\s*(\d+)(\+?)', re.IGNORECASE)


def normalize_cctv(name):
    """CCTV 频道名归一：CCTV-1 → CCTV1、CCTV1综合 → CCTV1、CCTV5+体育赛事 → CCTV5+。"""
    name = name.strip()
    m = CCTV_PREFIX.match(name)
    if m:
        return 'CCTV%s%s' % (m.group(1), m.group(2))
    if name.upper().startswith('CCTV'):
        return re.sub(r'^CCTV[\s\-－]+', 'CCTV', name, flags=re.IGNORECASE)
    return name


def cctv_key(name):
    """排序键：CCTV1 → CCTV2 → … → CCTV17，带后缀的排在主台之后。"""
    m = re.match(r'^CCTV(\d+)(.*)$', name, flags=re.IGNORECASE)
    if m:
        return (0, int(m.group(1)), m.group(2))
    return (1, 9999, name)


def line_name(line):
    return line.split(',', 1)[0].strip() if ',' in line else line.strip()


def fetch_upstream():
    """拉取作者最新 live.txt，全部失败返回 None。"""
    for url in UPSTREAM_URLS:
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=30) as resp:
                text = resp.read().decode('utf-8', 'ignore')
            if '#genre#' in text and len(text) > 1000:
                print('已拉取订阅: %s（%d 字节）' % (url.split('/')[2], len(text)))
                return text
            print('内容异常，跳过: %s' % url)
        except Exception as exc:  # noqa: BLE001
            print('拉取失败 %s: %s' % (url.split('/')[2], exc))
    return None


def read_lines(path):
    if not os.path.exists(path):
        return []
    with io.open(path, 'r', encoding='utf-8-sig', newline='') as f:
        raw = f.read()
    return raw.replace('\r\n', '\n').split('\n')


def split_blocks(lines):
    """把文件切成 [(分组头, [频道行]), ...]，分组头为空串表示文件开头的散行。"""
    blocks = []
    for line in lines:
        if '#genre#' in line:
            blocks.append([line, []])
        elif blocks:
            blocks[-1][1].append(line)
        else:
            blocks.append(['', [line]])
    return blocks


def find_block(blocks, keyword):
    for block in blocks:
        if block[0] and keyword in block[0]:
            return block
    return None


def block_pairs(block):
    """取出分组内的 (频道名, 链接)。"""
    pairs = []
    if not block:
        return pairs
    for line in block[1]:
        if ',' in line:
            name, url = line.split(',', 1)
            pairs.append((name.strip(), url))
    return pairs


def build(base_lines, fallback=None):
    """对 base_lines 应用规则，返回 (新行列表, 统计信息)。"""
    fallback = fallback or {}
    lines = [l for l in base_lines if l.strip()]

    # 0. 分组名去掉国旗/表情前缀（频道名只整理 IPV4线路 里的 CCTV，其它分组保持原样）
    fixed = []
    for line in lines:
        if '#genre#' in line:
            fixed.append(GROUP_PREFIX.sub('', line.split(',', 1)[0]).strip() + ',#genre#')
        else:
            fixed.append(line)
    blocks = split_blocks(fixed)

    # 1. 江苏地方分组：优先用组播线路里的源，取不到再用上一次的链接兜底
    js_old = dict(block_pairs(find_block(blocks, TARGET_GROUP)))
    js_old.update(fallback)

    found = {}
    src_block = find_block(blocks, SOURCE_GROUP)
    if src_block:
        for name, url in block_pairs(src_block):
            for src_name, _ in MAPPING:
                if name == src_name and src_name not in found:
                    found[src_name] = url
                    break
        moved = set('%s,%s' % (k, v) for k, v in found.items())
        src_block[1] = [l for l in src_block[1] if l not in moved]

    js_lines, missing = [], []
    for src_name, new_name in MAPPING:
        url = found.get(src_name) or js_old.get(new_name)
        if url:
            js_lines.append(new_name + ',' + url)
        else:
            missing.append(new_name)

    # 2. 删掉旧江苏地方分组、指定分组，以及所有河南频道
    kept, dropped, leftover_cctv, henan = [], {}, [], 0
    for block in blocks:
        head = block[0]
        if head and TARGET_GROUP in head:
            continue
        matched = [k for k in DROP_GROUPS if head and k in head]
        if matched:
            dropped[matched[0]] = len(block[1])
            continue
        if head and LEGACY_CCTV_GROUP in head:
            # 旧版本单独拆出来的央视频道，并回 IPV4线路
            leftover_cctv.extend(block[1])
            continue
        kept_lines = []
        for line in block[1]:
            if line.startswith(DROP_CHANNEL_PREFIX):
                henan += 1
                continue
            kept_lines.append(line)
        block[1] = kept_lines
        kept.append(block)

    iv4 = find_block(kept, IPV4_GROUP)
    if leftover_cctv:
        if iv4 is None:
            iv4 = ['%s,#genre#' % IPV4_GROUP, []]
            kept.append(iv4)
        iv4[1].extend(leftover_cctv)

    # 3. 只整理 IPV4线路：CCTV 去掉横线和中文后缀（CCTV1综合 → CCTV1），按台号排在最前
    cctv_count, renamed = 0, 0
    if iv4:
        cctv_lines, others, seen = [], [], set()
        for line in iv4[1]:
            if ',' not in line:
                others.append(line)
                continue
            name, url = line.split(',', 1)
            if name.strip().upper().startswith('CCTV'):
                new_name = normalize_cctv(name)
                if new_name != name.strip():
                    renamed += 1
                merged = new_name + ',' + url
                if merged in seen:
                    continue
                seen.add(merged)
                cctv_lines.append(merged)
                continue
            others.append(line)
        cctv_lines.sort(key=lambda l: cctv_key(line_name(l)))
        iv4[1] = cctv_lines + others
        cctv_count = len(cctv_lines)

    # 4. 组播线路 放到 IPV4线路 前面（其它分组顺序不变，重复执行结果一致）
    i_ipv4 = next((i for i, b in enumerate(kept) if b[0] and IPV4_GROUP in b[0]), None)
    i_src = next((i for i, b in enumerate(kept) if b[0] and SOURCE_GROUP in b[0]), None)
    if i_ipv4 is not None and i_src is not None and i_ipv4 < i_src:
        kept[i_ipv4], kept[i_src] = kept[i_src], kept[i_ipv4]

    # 5. 组装：江苏地方在最前
    out = ['%s,#genre#' % TARGET_GROUP] + js_lines
    for block in kept:
        if block[0]:
            out.append(block[0])
        out.extend(block[1])

    stats = {
        'dropped': dropped,
        'henan': henan,
        'renamed': renamed,
        'channels': len(js_lines),
        'missing': missing,
        'cctv': cctv_count,
    }
    return out, stats


def main():
    offline = '--offline' in sys.argv

    local_lines = read_lines(PATH)
    fallback = {}
    local_block = find_block(split_blocks([l for l in local_lines if l.strip()]), TARGET_GROUP)
    if local_block:
        fallback = dict(block_pairs(local_block))

    base_lines, source = local_lines, '本地 live.txt'
    if not offline:
        text = fetch_upstream()
        if text:
            base_lines, source = text.replace('\r\n', '\n').split('\n'), '作者订阅'
        else:
            print('作者地址拉取失败，改用本地 live.txt')
    if not base_lines:
        raise SystemExit('没有可用的 live.txt 数据')

    out, stats = build(base_lines, fallback)

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

    print('数据来源: %s' % source)
    print('删除分组: %s' % ('、'.join('%s(%s行)' % (k, v) for k, v in stats['dropped'].items()) or '无'))
    print('其它河南频道: %s 行' % stats['henan'])
    print('CCTV 改名（去横线/中文后缀）: %s 条' % stats['renamed'])
    print('IPV4线路 内 CCTV: %s 条（已按台号排序置顶）' % stats['cctv'])
    print('江苏地方: %s 个频道' % stats['channels'])
    if stats['missing']:
        print('缺少可用源: %s' % ', '.join(stats['missing']))
    print('已写入 %s（%s 行, %s 个分组）' % (PATH, len(out), genre_count))


if __name__ == '__main__':
    sys.exit(main())
