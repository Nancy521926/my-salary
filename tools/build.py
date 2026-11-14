"""无 Gradle 的 Android APK 构建脚本（aapt2 + javac + d8 + zipalign + apksigner）。

用法:
    export JAVA_HOME=/path/to/jdk-17
    export ANDROID_SDK=/path/to/Android/sdk     # 或 ANDROID_HOME
    python3 tools/build.py

需要 Android SDK 里装有 build-tools（含 aapt2/zipalign/lib/d8.jar/lib/apksigner.jar）
和至少一个 platform（platforms/android-*/android.jar）。
"""
import glob
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ANDROID = os.path.join(ROOT, "android")
WEB = os.path.join(ROOT, "web")
OUTPUT = os.path.join(ROOT, "output")

VERSION_CODE = "2"
VERSION_NAME = "1.1"
MIN_SDK = "24"
TARGET_SDK = "34"
PKG = "com.yili.payslip"
APK_NAME = "MySalary-v1.1.apk"

IS_WIN = os.name == "nt"
EXE = ".exe" if IS_WIN else ""

# 控制台/管道默认可能是 GBK，避免打印工具输出时报 UnicodeEncodeError
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def fail(msg):
    print(f"[ERROR] {msg}")
    sys.exit(1)


def find_sdk():
    sdk = os.environ.get("ANDROID_SDK") or os.environ.get("ANDROID_HOME")
    if not sdk:
        fail("请先设置 ANDROID_SDK（或 ANDROID_HOME）指向 Android SDK 根目录")
    if not os.path.isdir(sdk):
        fail(f"Android SDK 目录不存在: {sdk}")
    return sdk


def pick_build_tools(sdk):
    root = os.path.join(sdk, "build-tools")
    if not os.path.isdir(root):
        fail(f"找不到 {root}，请先安装 build-tools")
    pref = os.path.join(root, "34.0.0")
    if os.path.isdir(pref):
        return pref

    def ver_key(name):
        parts = []
        for p in name.split("."):
            parts.append(int(p) if p.isdigit() else 0)
        return parts

    dirs = [os.path.join(root, d) for d in os.listdir(root)
            if os.path.isdir(os.path.join(root, d))]
    if not dirs:
        fail(f"{root} 下没有 build-tools 版本")
    return sorted(dirs, key=lambda p: ver_key(os.path.basename(p)))[-1]


def pick_platform_jar(sdk):
    root = os.path.join(sdk, "platforms")
    if not os.path.isdir(root):
        fail(f"找不到 {root}，请先安装 platform（如 android-34）")

    def ver_key(name):
        tail = name.rsplit("-", 1)[-1]
        return int(tail) if tail.isdigit() else 0

    cands = []
    for d in os.listdir(root):
        jar = os.path.join(root, d, "android.jar")
        if os.path.isfile(jar):
            cands.append((ver_key(d), jar))
    if not cands:
        fail(f"{root} 下没有可用的 android.jar")
    return sorted(cands)[-1][1]


SDK = find_sdk()
BT = pick_build_tools(SDK)
PLATFORM_SRC = pick_platform_jar(SDK)

JAVA_HOME = os.environ.get("JAVA_HOME")
if not JAVA_HOME:
    fail("请先设置 JAVA_HOME 指向 JDK 17")
JAVAC = os.path.join(JAVA_HOME, "bin", "javac" + EXE)
JAVA = os.path.join(JAVA_HOME, "bin", "java" + EXE)
KEYTOOL = os.path.join(JAVA_HOME, "bin", "keytool" + EXE)
for tool in (JAVAC, JAVA, KEYTOOL):
    if not os.path.isfile(tool):
        fail(f"找不到 {tool}")

AAPT2 = os.path.join(BT, "aapt2" + EXE)
ZIPALIGN = os.path.join(BT, "zipalign" + EXE)
D8_JAR = os.path.join(BT, "lib", "d8.jar")
APKSIGNER_JAR = os.path.join(BT, "lib", "apksigner.jar")
LAMBDA_STUBS = os.path.join(BT, "core-lambda-stubs.jar")
for path in (AAPT2, ZIPALIGN, D8_JAR, APKSIGNER_JAR):
    if not os.path.isfile(path):
        fail(f"build-tools 缺文件: {path}")

# aapt2 不认中文路径：构建在工作副本里做。
_work = os.path.join(tempfile.gettempdir(), "payslip_build")
if not os.path.abspath(__file__).isascii() or not _work.isascii():
    _work = r"C:\tmp\payslip_build" if IS_WIN else "/tmp/payslip_build"
WORK = _work
BUILD = os.path.join(WORK, "build")
ANDROID_W = os.path.join(WORK, "android")
PLATFORM = os.path.join(WORK, "android.jar")

# 签名密钥跨次构建复用：每次换新钥会导致新旧 APK 签名不同，手机无法覆盖安装
KEYSTORE = os.path.join(os.path.expanduser("~"), ".payslip-build", "debug.keystore")


def run(cmd, cwd=None, what=""):
    print(f"\n>>> {what or ' '.join(cmd[:3])}")
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    out = (p.stdout or "") + (p.stderr or "")
    if out.strip():
        print(out.strip()[:4000])
    if p.returncode != 0:
        print(f"[FAILED] exit={p.returncode}")
        sys.exit(1)
    return out


def step_prepare():
    if os.path.isdir(WORK):
        shutil.rmtree(WORK)
    for d in ("assets", "compiled", "gen", "classes", "dex"):
        os.makedirs(os.path.join(BUILD, d), exist_ok=True)

    # 源码树整份复制到 ASCII 工作目录
    shutil.copytree(ANDROID, ANDROID_W)
    shutil.copyfile(PLATFORM_SRC, PLATFORM)
    src = os.path.join(WEB, "index.html")
    dst = os.path.join(BUILD, "assets", "index.html")
    shutil.copyfile(src, dst)
    print(f"工作目录 {WORK}")
    print(f"build-tools {BT}")
    print(f"android.jar {PLATFORM_SRC}")
    print(f"assets/index.html  {os.path.getsize(dst):,} bytes")


def step_compile_res():
    run([AAPT2, "compile", "--dir", os.path.join(ANDROID_W, "res"),
         "-o", os.path.join(BUILD, "compiled", "res.zip")],
        what="aapt2 compile（资源编译）")


def step_link():
    run([AAPT2, "link",
         "-o", os.path.join(BUILD, "base.apk"),
         "-I", PLATFORM,
         "--manifest", os.path.join(ANDROID_W, "AndroidManifest.xml"),
         "-A", os.path.join(BUILD, "assets"),
         "--java", os.path.join(BUILD, "gen"),
         "--min-sdk-version", MIN_SDK,
         "--target-sdk-version", TARGET_SDK,
         "--version-code", VERSION_CODE,
         "--version-name", VERSION_NAME,
         os.path.join(BUILD, "compiled", "res.zip")],
        what="aapt2 link（打包资源 + 清单 + assets）")


def step_javac():
    sources = sorted(glob.glob(os.path.join(ANDROID_W, "src", "**", "*.java"), recursive=True))
    sources += sorted(glob.glob(os.path.join(BUILD, "gen", "**", "*.java"), recursive=True))
    if not sources:
        fail("没有找到 java 源文件")
    classes = os.path.join(BUILD, "classes")
    run([JAVAC, "-nowarn", "-encoding", "UTF-8",
         "-source", "8", "-target", "8",
         "-bootclasspath", PLATFORM + os.pathsep + LAMBDA_STUBS,
         "-d", classes] + sources,
        what=f"javac（编译 {len(sources)} 个 java 文件）")


def step_d8():
    classfiles = sorted(glob.glob(os.path.join(BUILD, "classes", "**", "*.class"), recursive=True))
    if not classfiles:
        fail("没有 class 文件")
    run([JAVA, "-cp", D8_JAR, "com.android.tools.r8.D8",
         "--lib", PLATFORM,
         "--min-api", MIN_SDK,
         "--release",
         "--output", os.path.join(BUILD, "dex")] + classfiles,
        what=f"d8（{len(classfiles)} 个 class → classes.dex）")


def step_pack():
    apk = os.path.join(BUILD, "base.apk")
    dex = os.path.join(BUILD, "dex", "classes.dex")
    with zipfile.ZipFile(apk, "a", zipfile.ZIP_DEFLATED) as z:
        z.write(dex, "classes.dex")
    print(f"\n>>> 写入 classes.dex（{os.path.getsize(dex):,} bytes）")


def step_align_sign():
    aligned = os.path.join(BUILD, "aligned.apk")
    run([ZIPALIGN, "-f", "-p", "4",
         os.path.join(BUILD, "base.apk"), aligned],
        what="zipalign（4 字节对齐）")

    if not os.path.exists(KEYSTORE):
        os.makedirs(os.path.dirname(KEYSTORE), exist_ok=True)
        run([KEYTOOL, "-genkeypair", "-v",
             "-keystore", KEYSTORE,
             "-storepass", "android", "-keypass", "android",
             "-alias", "androiddebugkey",
             "-keyalg", "RSA", "-keysize", "2048", "-validity", "10000",
             "-dname", "CN=Android Debug,O=Android,C=US"],
            what="keytool（生成调试签名）")

    signed = os.path.join(WORK, "signed.apk")
    run([JAVA, "-Dfile.encoding=UTF-8", "-cp", APKSIGNER_JAR,
         "com.android.apksigner.ApkSignerTool", "sign",
         "--ks", KEYSTORE,
         "--ks-pass", "pass:android",
         "--ks-key-alias", "androiddebugkey",
         "--key-pass", "pass:android",
         "--min-sdk-version", MIN_SDK,
         "--out", signed, aligned],
        what="apksigner（V1+V2+V3 签名）")
    run([JAVA, "-Dfile.encoding=UTF-8", "-cp", APKSIGNER_JAR,
         "com.android.apksigner.ApkSignerTool", "verify",
         "--min-sdk-version", MIN_SDK, signed],
        what="apksigner verify（校验签名）")

    os.makedirs(OUTPUT, exist_ok=True)
    final = os.path.join(OUTPUT, APK_NAME)
    shutil.copyfile(signed, final)
    print(f"\nAPK: {final}  ({os.path.getsize(final):,} bytes)")
    return final


def main():
    step_prepare()
    step_compile_res()
    step_link()
    step_javac()
    step_d8()
    step_pack()
    step_align_sign()
    print("\n构建完成")


if __name__ == "__main__":
    main()
