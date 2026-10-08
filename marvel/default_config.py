import os
import sys

_MARVEL_HOME = os.path.join(os.path.expanduser("~"), ".marvel")


def _can_create_files(directory: str) -> bool:
    """Can a *new* file actually be created here?

    ``os.access(dir, os.W_OK)`` is not the test — on Windows it does not evaluate
    ACLs, and it says "writable" for directories where ``CreateFile`` is refused by
    something outside the ACL (a security product's file protection, a filter
    driver). Verified on 2026-10-08: ``~/.marvel`` reported W_OK=True while every
    ``os.open(..., O_CREAT|O_EXCL)`` returned WinError 5. Only a real create proves it.
    """
    try:
        os.makedirs(directory, exist_ok=True)
        probe = os.path.join(directory, f".marvel_write_probe_{os.getpid()}")
        fd = os.open(probe, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        os.unlink(probe)
        return True
    except OSError:
        return False


def _resolve_state_dir(preferred: str, fallback: str, label: str) -> str:
    """Use ``preferred`` when it accepts new files, otherwise a writable fallback.

    Without this, a blocked ``~/.marvel`` made every analysis fail with a raw
    ``[WinError 5] 拒绝访问`` while creating ``logs/<ticker>`` — the app was unusable
    for a reason that has nothing to do with the app. The fallback lives beside the
    code instead of in the user profile, so it is reachable on a machine where the
    profile directory is protected; and it says out loud where the reports went,
    because silently writing somewhere else is how "my history disappeared" happens.
    """
    if _can_create_files(preferred):
        return preferred
    if _can_create_files(fallback):
        print(
            f"[marvel] 警告：{label} 目录无法新建文件（{preferred}）——"
            f"通常是安全软件/权限拦截。已改用 {fallback}。\n"
            f"[marvel] 想用回原目录，请把它加入安全软件白名单，或设置环境变量覆盖路径。",
            file=sys.stderr,
            flush=True,
        )
        return fallback
    # Neither works: keep the configured value so the error names the path the user
    # expects, and let the caller report it with context.
    return preferred


def _can_append(path: str) -> bool:
    """Can we write to ``path`` — appending if it exists, creating it if not?

    The memory log is *appended* to an existing file, and on a machine where new
    files are refused an append can still work. Testing creation there would move a
    working memory log somewhere else for no reason.
    """
    try:
        if os.path.exists(path):
            with open(path, "a", encoding="utf-8"):
                pass
            return True
        return _can_create_files(os.path.dirname(path))
    except OSError:
        return False


def _resolve_state_file(preferred: str, fallback: str, label: str) -> str:
    """File-level counterpart of :func:`_resolve_state_dir`."""
    if _can_append(preferred):
        return preferred
    if _can_append(fallback):
        print(
            f"[marvel] 警告：{label} 无法写入（{preferred}）——已改用 {fallback}。",
            file=sys.stderr,
            flush=True,
        )
        return fallback
    return preferred


_PROJECT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_FALLBACK_HOME = os.path.join(_PROJECT_DIR, ".marvel_state")

_RESULTS_DIR = _resolve_state_dir(
    os.getenv("MARVEL_RESULTS_DIR", os.path.join(_MARVEL_HOME, "logs")),
    os.path.join(_FALLBACK_HOME, "logs"),
    "报告输出",
)
_CACHE_DIR = _resolve_state_dir(
    os.getenv("MARVEL_CACHE_DIR", os.path.join(_MARVEL_HOME, "cache")),
    os.path.join(_FALLBACK_HOME, "cache"),
    "数据缓存",
)
_MEMORY_PATH = _resolve_state_file(
    os.getenv("MARVEL_MEMORY_LOG_PATH", os.path.join(_MARVEL_HOME, "memory", "trading_memory.md")),
    os.path.join(_FALLBACK_HOME, "memory", "trading_memory.md"),
    "记忆日志",
)

DEFAULT_CONFIG = {
    "project_dir": os.path.abspath(os.path.join(os.path.dirname(__file__), ".")),
    "results_dir": _RESULTS_DIR,
    "data_cache_dir": _CACHE_DIR,
    #: Where reports went *before* any fallback — the history list still reads it, so
    #: analyses saved while the profile directory was writable stay visible.
    "legacy_results_dir": os.path.join(_MARVEL_HOME, "logs"),
    #: True when `results_dir` is not the configured default (surfaced in the UI).
    "results_dir_fell_back": os.path.normcase(_RESULTS_DIR)
    != os.path.normcase(os.getenv("MARVEL_RESULTS_DIR", os.path.join(_MARVEL_HOME, "logs"))),
    "memory_log_path": _MEMORY_PATH,
    # Optional cap on the number of resolved memory log entries. When set,
    # the oldest resolved entries are pruned once this limit is exceeded.
    # Pending entries are never pruned. None disables rotation entirely.
    "memory_log_max_entries": None,
    # LLM settings.
    # `LLM_PROVIDER` is read so a container/orchestrator can select the provider
    # without editing code — `docker-compose.yml`'s ollama profile has always set
    # it, but nothing read it, so that profile silently ran on the OpenAI default.
    "llm_provider": os.getenv("LLM_PROVIDER", "openai"),
    "deep_think_llm": os.getenv("DEEP_THINK_LLM", "gpt-5.4"),
    "quick_think_llm": os.getenv("QUICK_THINK_LLM", "gpt-5.4-mini"),
    # Explicit endpoint override. `.env.example` has documented BACKEND_URL all
    # along and the Web UI read it, but this dict did not — so a CLI or container
    # run ignored it. Needed, for instance, to point the ollama provider at a
    # sibling container instead of the client's localhost default.
    #
    # When None, each provider's client falls back to its own default endpoint
    # (api.openai.com for OpenAI, generativelanguage.googleapis.com for Gemini, ...).
    # The CLI overrides this per provider when the user picks one. Keeping a
    # provider-specific URL here would leak (e.g. OpenAI's /v1 was previously
    # being forwarded to Gemini, producing malformed request URLs).
    "backend_url": os.getenv("BACKEND_URL") or None,
    # Provider-specific thinking configuration
    "google_thinking_level": None,      # "high", "minimal", etc.
    "openai_reasoning_effort": None,    # "medium", "high", "low"
    "anthropic_effort": None,           # "high", "medium", "low"
    # Checkpoint/resume: when True, LangGraph saves state after each node
    # so a crashed run can resume from the last successful step.
    "checkpoint_enabled": False,
    # Output language for analyst reports and final decision
    # Internal agent debate stays in English for reasoning quality
    "output_language": "Chinese",
    # Debate and discussion settings
    "max_debate_rounds": 1,
    "max_risk_discuss_rounds": 1,
    # LangGraph step budget. A nine-analyst run is long: each analyst is at
    # least node -> tools -> node (three steps, seven if it makes three tool
    # calls), plus the quality gate, both debates, the trader and the PM. The
    # old value of 100 was reachable in a routine run, and hitting the cap raises
    # GraphRecursionError mid-graph, which loses the whole multi-minute analysis
    # when checkpointing is off (the default).
    "max_recur_limit": 250,
    # Data vendor configuration
    # Category-level configuration (default for all tools in category)
    "data_vendors": {
        "core_stock_apis": "a_stock",        # Options: a_stock, alpha_vantage, yfinance
        "technical_indicators": "a_stock",   # Options: a_stock, alpha_vantage, yfinance
        "fundamental_data": "a_stock",       # Options: a_stock, alpha_vantage, yfinance
        "news_data": "a_stock",              # Options: a_stock, alpha_vantage, yfinance
        "signal_data": "a_stock",            # A-stock only: topic attribution, capital flow, consensus
    },
    # Tool-level configuration (takes precedence over category-level)
    "tool_vendors": {
        # Example: "get_stock_data": "alpha_vantage",  # Override category default
    },
}
