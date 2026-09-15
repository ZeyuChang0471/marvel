#!/usr/bin/env bash
# MARVEL 一键启动/批量分析脚本 (Git Bash / WSL / Linux)
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# ── 颜色 ──────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

ok()   { echo -e "${GREEN}[OK]${NC} $*"; }
info() { echo -e "${CYAN}[INFO]${NC} $*"; }
err()  { echo -e "${RED}[ERROR]${NC} $*"; }

# ── 运行函数（必须在调用前定义）───────────────────────────────────

run_web() {
    echo ""
    echo "════════════════════════════════════════════════════════"
    echo "  启动 Streamlit Web UI → http://localhost:8501"
    echo "  按 Ctrl+C 停止"
    echo "════════════════════════════════════════════════════════"
    echo ""
    $PYTHON -m streamlit run web/app.py --server.port 8501
}

run_single() {
    echo ""
    read -r -p "请输入股票代码或名称 (如 600519 或 贵州茅台): " TICKER
    if [ -z "$TICKER" ]; then
        err "未输入股票代码"
        exit 1
    fi

    read -r -p "请输入分析日期 (格式 YYYY-MM-DD，直接回车=今天): " DATE
    if [ -z "$DATE" ]; then
        DATE=$(date +%Y-%m-%d)
    fi

    echo ""
    echo "════════════════════════════════════════════════════════"
    echo "  开始分析: $TICKER ($DATE)"
    echo "════════════════════════════════════════════════════════"
    echo ""

    $PYTHON run_single.py "$TICKER" "$DATE" || {
        echo ""
        err "分析失败，请查看上方错误信息"
        exit 1
    }

    echo ""
    ok "分析完成！结果保存在 .marvel/results/ 目录"
}

run_batch() {
    echo ""

    # 检查 stocks.txt
    if [ ! -f "stocks.txt" ]; then
        info "未找到 stocks.txt，正在创建示例文件..."
        cat > stocks.txt << 'STOCKSEOF'
# 批量分析股票列表 — 每行一个，支持代码或中文名
# 以 # 开头的行为注释，空行会自动跳过
600519  # 贵州茅台
000858  # 五粮液
300750  # 宁德时代
STOCKSEOF
        info "已创建 stocks.txt (路径: $PWD/stocks.txt)，请编辑后重新运行选项 [3]"
        exit 0
    fi

    # 提取有效股票列表（兼容 Git Bash，不用 mapfile）
    STOCKS=()
    while IFS= read -r line; do
        # 跳过空行和注释行
        [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
        # 取第一个字段（空格/tab 前）
        ticker=$(echo "$line" | awk '{print $1}')
        STOCKS+=("$ticker")
    done < stocks.txt
    TOTAL=${#STOCKS[@]}

    if [ "$TOTAL" -eq 0 ]; then
        err "stocks.txt 中没有有效的股票代码"
        exit 1
    fi

    info "待分析股票数: $TOTAL 只"
    echo ""

    PASSED=0
    FAILED=0
    CURRENT=0

    for TICKER in "${STOCKS[@]}"; do
        CURRENT=$((CURRENT + 1))
        echo ""
        echo "════════════════════════════════════════════════════════"
        echo "  [$CURRENT/$TOTAL] 正在分析: $TICKER"
        echo "════════════════════════════════════════════════════════"

        if $PYTHON run_single.py "$TICKER"; then
            PASSED=$((PASSED + 1))
            ok "$TICKER 分析成功"
        else
            FAILED=$((FAILED + 1))
            err "$TICKER 分析失败，继续下一个..."
        fi
    done

    echo ""
    echo "════════════════════════════════════════════════════════"
    echo "  批量分析完成！"
    echo "  总计: $TOTAL  成功: $PASSED  失败: $FAILED"
    echo "  结果保存在 .marvel/results/ 目录"
    echo "════════════════════════════════════════════════════════"
    echo ""
}

# ═══════════════════════════════════════════════════════════════════
#  主流程
# ═══════════════════════════════════════════════════════════════════

echo ""
echo "  ╔══════════════════════════════════════════════════════════════╗"
echo "  ║      MARVEL — A股多Agent投研框架             ║"
echo "  ║      LLM: DeepSeek V4 Pro + V4 Flash  语言: 中文           ║"
echo "  ╚══════════════════════════════════════════════════════════════╝"
echo ""

# ── 1. 检查 venv ───────────────────────────────────────────────────
PYTHON=""
if [ -f "venv/Scripts/python.exe" ]; then
    PYTHON="venv/Scripts/python.exe"
elif [ -f "venv/bin/python" ]; then
    PYTHON="venv/bin/python"
else
    err "未找到虚拟环境，请先运行部署: python -m venv venv && pip install -e ."
    exit 1
fi
ok "虚拟环境: $PYTHON"

# ── 2. 激活 venv ───────────────────────────────────────────────────
if [ -f "venv/Scripts/activate" ]; then
    source venv/Scripts/activate
else
    source venv/bin/activate
fi

# ── 3. 检查 .env ───────────────────────────────────────────────────
if [ ! -f ".env" ]; then
    err ".env 文件不存在，请先创建并配置 DEEPSEEK_API_KEY"
    exit 1
fi

API_KEY=$(grep -E '^DEEPSEEK_API_KEY\s*=\s*sk-' .env 2>/dev/null | head -1 || true)
if [ -z "$API_KEY" ]; then
    err ".env 中 DEEPSEEK_API_KEY 未设置或格式不正确"
    echo "   请编辑 .env 文件，填入: DEEPSEEK_API_KEY=sk-你的key"
    exit 1
fi
ok "DEEPSEEK_API_KEY 已配置"

# ── 4. 模式选择 ───────────────────────────────────────────────────
echo ""
echo "  ┌──────────────────────────────────────────────────┐"
echo "  │  请选择运行模式:                                 │"
echo "  │                                                  │"
echo "  │  [1] Web UI — 浏览器图形界面分析                 │"
echo "  │  [2] 单股分析 — 命令行分析单只股票               │"
echo "  │  [3] 批量分析 — 从 stocks.txt 批量跑分析         │"
echo "  │  [0] 退出                                        │"
echo "  └──────────────────────────────────────────────────┘"
echo ""

read -r -p "请输入选项 (0-3): " MODE

case "$MODE" in
    1) run_web ;;
    2) run_single ;;
    3) run_batch ;;
    0) exit 0 ;;
    *) err "无效选项: $MODE"; exit 1 ;;
esac
