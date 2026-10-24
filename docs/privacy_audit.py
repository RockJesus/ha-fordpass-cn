# -*- coding: utf-8 -*-
"""FordPass-CN 仓库全局个人信息脱敏审计（2026-10-24 固化入库，v5.6.5 增 dangling 扫描）。

用法：python3 -X utf8 -u docs/privacy_audit.py [--dangling <sha> ...]   （需 GITHUB_TOKEN 环境变量）
检查：真实 VIN（LVSH 前缀非 FAKE）/ 手机号（字段上下文）/ 密码值 / 身份证 / 邮箱
范围：① 全 git 可达历史内容+message ② release body/assets ③ contents API 抽查
      ④ codeload zip 抽查 ⑤ **dangling commit 扫描**（filter-repo 重写后的不可达
      旧 commit 仍可按 SHA 从 GitHub 读取——git rev-list 扫不到，必须按已知 hash
      逐一 API 点查；命中即 FAIL 并提示提交 GitHub Support 清除）。
白名单：示例假号（138/139/137 开头固定前缀段，见 FAKE_PREFIXES）与 LVSHFAKE 假 VIN。
"""
import base64, io, json, os, re, subprocess, sys, tempfile, urllib.error, urllib.request, zipfile

TOKEN = os.environ.get("GITHUB_TOKEN", "")
REPO = "RockJesus/ha-fordpass-cn"
VIN_RE = re.compile(r"LVSH[A-Z0-9]{13}")
FIELD_PHONE_RE = re.compile(r'"(?:phone|phoneNumber|nickPhone|mobile|tel)"\s*:\s*"?([1-3][0-9]{9})"')
PHONE_ANY_RE = re.compile(r"(?<![0-9a-fA-F])1[3-9][0-9]{9}(?![0-9a-fA-F])")
# 示例假号白名单：用前缀匹配避免源码出现完整号码形态（仓库零号码字符串）
FAKE_PREFIXES = ("13800000", "13800138", "13900000", "13700000")


def _is_fake(n: str) -> bool:
    return any(n.startswith(p) for p in FAKE_PREFIXES)
H = {"Authorization": "Bearer " + TOKEN, "Accept": "application/vnd.github+json"}

# 已知 filter-repo 重写后仍可访问的旧 commit（dangling object，GitHub 按 SHA 提供）——
# 注意：不得把具体 SHA 硬编码入库（等于公开含敏感数据的旧 commit 访问索引）。
# 审计时用 --dangling <sha> 参数临时传入；待 GitHub Support purge 生效后移除对应项。
KNOWN_DANGLING: list[str] = []


def gh(url):
    req = urllib.request.Request(url, headers=H)
    try:
        return json.load(urllib.request.urlopen(req, timeout=60))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def scan_text(t: str) -> tuple[set, set, set]:
    vin = {m.group(0) for m in VIN_RE.finditer(t) if not m.group(0).startswith("LVSHFAKE")}
    fp = {m.group(1) for m in FIELD_PHONE_RE.finditer(t) if not _is_fake(m.group(1))}
    ap = {m.group(0) for m in PHONE_ANY_RE.finditer(t) if not _is_fake(m.group(0))}
    return vin, fp, ap


def scan_dangling(shas: list[str]) -> list[str]:
    """按已知旧 commit hash 逐一点查：git/trees 递归拿全树 -> contents 逐文件扫描。
    返回含敏感数据的 commit 列表（应提交 GitHub Support purge）。"""
    bad = []
    for sha in shas:
        tree = gh(f"https://api.github.com/repos/{REPO}/git/trees/{sha}?recursive=1")
        if tree is None:
            print(f"    dangling {sha}: 不可访问（已清除 ✅）")
            continue
        blobs = [p["path"] for p in tree.get("tree", []) if p.get("type") == "blob"]
        found = set()
        for path in blobs:
            if not path.endswith((".py", ".md", ".json", ".yaml", ".yml")):
                continue
            cj = gh(f"https://api.github.com/repos/{REPO}/contents/{path}?ref={sha}")
            if cj is None:
                continue
            t = base64.b64decode(cj.get("content") or "").decode("utf-8", "ignore")
            v, f, a = scan_text(t)
            if v or f or a:
                found.add(path)
                print(f"      {sha}:{path} 敏感={sorted(v) or sorted(f) or sorted(a)}")
        if found:
            bad.append(sha)
            print(f"    dangling {sha}: **仍可读且含敏感数据（需 GitHub Support purge）**")
        else:
            print(f"    dangling {sha}: 可访问但内容干净 ✅")
    return bad


def main() -> int:
    if not TOKEN:
        print("FAIL: 需要 GITHUB_TOKEN 环境变量"); return 1
    extra = []
    if "--dangling" in sys.argv:
        i = sys.argv.index("--dangling")
        extra = sys.argv[i + 1:]
    shas = list(dict.fromkeys(KNOWN_DANGLING + extra))
    tmp = tempfile.mkdtemp(prefix="vin_audit_")
    print(f"[1] mirror clone 线上仓库 -> {tmp}")
    subprocess.run(["git", "clone", "--mirror", f"https://github.com/{REPO}.git", f"{tmp}/fp.git"], check=True, capture_output=True)
    git = ["git", "-C", f"{tmp}/fp.git"]
    all_c = subprocess.run([*git, "rev-list", "--all"], capture_output=True, text=True).stdout.split()
    print(f"    commit={len(all_c)} tag={subprocess.run([*git,'tag'],capture_output=True,text=True).stdout.count(chr(10))}")
    vin_bad, phone_bad, any_bad = set(), set(), set()
    B = 300
    pat = r'LVSH[A-Z0-9]{13}|"(?:phone|phoneNumber|nickPhone|mobile|tel)"\s*:\s*"?1[3-9][0-9]{9}|(?<![0-9a-fA-F])1[3-9][0-9]{9}(?![0-9a-fA-F])'
    for i in range(0, len(all_c), B):
        out = subprocess.run([*git, "grep", "-n", "-E", pat, *all_c[i:i+B]], capture_output=True, text=True)
        for m in VIN_RE.finditer(out.stdout):
            if not m.group(0).startswith("LVSHFAKE"): vin_bad.add(m.group(0))
        for m in FIELD_PHONE_RE.finditer(out.stdout):
            if not _is_fake(m.group(1)): phone_bad.add(m.group(1))
        for m in PHONE_ANY_RE.finditer(out.stdout):
            if not _is_fake(m.group(0)): any_bad.add(m.group(0))
    msg = subprocess.run([*git, "log", "--all", "--format=%s"], capture_output=True, text=True).stdout
    msg_bad = [m for m in VIN_RE.finditer(msg) if not m.group(0).startswith("LVSHFAKE")]
    print(f"[2] 历史内容: 真实VIN={sorted(vin_bad) or 0} phone字段={sorted(phone_bad) or 0} 任意号码={sorted(any_bad) or 0} message={len(msg_bad)}")
    # release body / assets
    bad_rel, assets = [], 0
    page = 1
    while True:
        batch = gh(f"https://api.github.com/repos/{REPO}/releases?per_page=100&page={page}")
        if not batch: break
        for r in batch:
            body = (r.get("body") or "") + (r.get("name") or "")
            if VIN_RE.search(body) or PHONE_ANY_RE.search(body): bad_rel.append(r["tag_name"])
            assets += len(r.get("assets") or [])
        page += 1
    print(f"[3] release: body敏感={bad_rel or 0} assets={assets}")
    # contents 抽查
    ok = skip = 0
    for ref in ("main", "v4.9.7", "v5.5.2", "v5.6.2"):
        for f in ("tests/test_wbsk.py", "tests/test_init.py", "README.md"):
            cj = gh(f"https://api.github.com/repos/{REPO}/contents/{f}?ref={ref}")
            if cj is None: skip += 1; continue
            t = base64.b64decode(cj["content"]).decode("utf-8")
            v, fp, ap = scan_text(t)
            assert not v and not fp and not ap and not t.startswith("\ufeff"), f"{ref}/{f}: {v}{fp}{ap}"
            ok += 1
    print(f"[4] contents: {ok} 通过 / {skip} 跳过")
    # codeload zip
    for v in ("v2.5.9", "v3.0.0", "v4.9.7", "v5.5.2", "v5.6.2"):
        data = urllib.request.urlopen(f"https://codeload.github.com/{REPO}/zip/refs/tags/{v}", timeout=60).read()
        z = zipfile.ZipFile(io.BytesIO(data)); hit = 0
        for nm in z.namelist():
            if not nm.endswith((".py", ".md", ".json", ".yaml", ".yml")): continue
            t = z.read(nm).decode("utf-8", "ignore")
            v, fp, ap = scan_text(t)
            if v or fp or ap: hit += 1
        print(f"[5] codeload {v}: {hit} {'PASS' if hit==0 else 'FAIL'}")
    # dangling 扫描（filter-repo 后旧 commit 仍可读，rev-list 不可见）
    print(f"[6] dangling 扫描（{len(shas)} 个已知旧 commit）:")
    dang_bad = scan_dangling(shas)
    ok_all = not (vin_bad or phone_bad or any_bad or msg_bad or bad_rel or assets or dang_bad)
    print("RESULT:", "PASS（可达历史与发布物无个人信息）" if ok_all else "FAIL")
    if dang_bad:
        print("  待 GitHub Support purge：", ", ".join(dang_bad))
        print("  处理完成后从 --dangling 移除对应项并重跑本脚本")
    return 0


if __name__ == "__main__":
    sys.exit(main())
