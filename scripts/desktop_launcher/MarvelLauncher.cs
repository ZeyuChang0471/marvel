// MARVEL desktop launcher.
//
// Upgrades the old MARVEL.bat. The bat refused to start when port 8501 was already
// busy ("close the other instance first"), which is the wrong answer when the thing
// already listening *is* MARVEL: the right move is to open the browser. It also
// showed the server's log only in a console that vanished when the window closed,
// so after a freeze there was nothing left to look at — the 2026-10-08 hang had to
// be diagnosed without the server's stderr for exactly that reason.
//
// This launcher:
//   * is single-instance aware (healthy server -> just open the browser);
//   * notices a *foreign* service on 8501 and says so instead of failing cryptically;
//   * waits for the health endpoint before opening the browser, so the tab never
//     lands on a "connection refused" page;
//   * tees the server's stdout/stderr to the console *and* to
//     ~/.marvel/logs/web_ui.log, so a crash or hang leaves evidence;
//   * keeps the window open on exit and on failure, with the last log lines.
//
// Compile with the .NET Framework compiler (C# 5 syntax only, no string
// interpolation, no ?.):
//   csc.exe /target:exe /codepage:65001 /win32icon:assets\marvel.ico /out:MARVEL.exe MarvelLauncher.cs

using System;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;

internal static class MarvelLauncher
{
    private const string AppUrl = "http://127.0.0.1:8501";
    private const string HealthUrl = "http://127.0.0.1:8501/_stcore/health";
    private const int HealthPort = 8501;

    /// <summary>How long to wait for the UI to answer before giving up.</summary>
    private const int ReadyTimeoutSeconds = 150;

    private static string _logPath;
    private static readonly object LogLock = new object();

    private static int Main(string[] args)
    {
        // Deliberately do NOT force Console.OutputEncoding = UTF8.
        //
        // On a Chinese Windows the console runs code page 936, so Chinese written in
        // the console's own encoding renders correctly. Forcing UTF-8 there is a
        // classic way to turn every Chinese line into mojibake, and when stdout is
        // redirected it produces UTF-8 bytes that the reading side (PowerShell's
        // Get-Content, a log viewer, `>` redirection) decodes as ANSI — which is
        // exactly what happened in the first build of this launcher.
        //
        // The log file is a different matter: it is written explicitly as UTF-8, so
        // it is readable regardless of the console's code page.
        Banner();

        string repo = ResolveRepoRoot();
        if (repo == null)
        {
            Fail("找不到 MARVEL 项目目录。请把 MARVEL.exe 放到项目里，或设置环境变量 MARVEL_HOME 指向仓库根目录。");
            return 2;
        }
        Console.WriteLine("[MARVEL] 项目目录: " + repo);

        string launcher = Path.Combine(repo, @"venv\Scripts\marvel-web.exe");
        if (!File.Exists(launcher))
        {
            Fail("找不到 " + launcher + "\r\n（虚拟环境没有装好？先运行一次安装脚本。）");
            return 2;
        }

        _logPath = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
            @".marvel\logs\web_ui.log");

        // ── Already running? Then this click means "show me the UI" ──────────────
        if (IsHealthy(1500))
        {
            Console.WriteLine("[MARVEL] 服务已经在运行，直接打开浏览器。");
            Console.WriteLine("[MARVEL] （不会再启动第二个实例——套两层 streamlit 会得到空白页。）");
            OpenBrowser();
            Pause("按回车关闭本窗口（服务继续在后台运行）。");
            return 0;
        }

        if (PortIsOpen())
        {
            Fail("端口 " + HealthPort + " 被占用，但它不是 MARVEL（健康检查没有响应）。\r\n"
                + "请先关掉占用该端口的程序，或改用其它端口启动。");
            return 3;
        }

        Console.WriteLine("[MARVEL] 启动服务，日志同时写入:");
        Console.WriteLine("         " + _logPath);
        Console.WriteLine("[MARVEL] 就绪后会自动打开 " + AppUrl);
        Console.WriteLine("[MARVEL] 按 Ctrl+C 可停止服务。");
        Console.WriteLine("---------------------------------------------------------------");
        Log("=========== 启动于 " + DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss", CultureInfo.InvariantCulture) + " ===========");

        Process child = StartServer(launcher, repo);
        if (child == null)
        {
            Fail("服务启动失败（进程没能创建）。");
            return 4;
        }

        bool cancelled = false;
        Console.CancelKeyPress += delegate(object sender, ConsoleCancelEventArgs e)
        {
            // Handle the shutdown ourselves so the child is killed deliberately
            // rather than left orphaned holding port 8501.
            e.Cancel = true;
            cancelled = true;
        };

        bool ready = WaitUntilReady(child, ref cancelled);

        if (cancelled)
        {
            Stop(child);
            Console.WriteLine();
            Console.WriteLine("[MARVEL] 已停止服务。");
            Pause("按回车关闭本窗口。");
            return 0;
        }

        if (!ready)
        {
            if (HasExited(child))
            {
                Console.WriteLine();
                Console.WriteLine("[MARVEL] 服务启动后立刻退出了（退出码 " + child.ExitCode + "）。");
            }
            else
            {
                Console.WriteLine();
                Console.WriteLine("[MARVEL] " + ReadyTimeoutSeconds + " 秒内没有就绪，正在停止它。");
                Stop(child);
            }
            TailLog(20);
            Pause("按回车关闭本窗口。");
            return 5;
        }

        Console.WriteLine("---------------------------------------------------------------");
        Console.WriteLine("[MARVEL] 已就绪: " + AppUrl);
        OpenBrowser();

        // Stay in the foreground: Ctrl+C stops it, and this window is the only place
        // the server's output is visible live.
        while (!child.HasExited && !cancelled)
        {
            Thread.Sleep(400);
        }

        if (cancelled)
        {
            Stop(child);
        }

        Console.WriteLine();
        if (!cancelled)
        {
            Console.WriteLine("[MARVEL] 服务已退出（退出码 " + child.ExitCode + "）。");
            TailLog(15);
        }
        else
        {
            Console.WriteLine("[MARVEL] 已停止服务。");
        }
        Pause("按回车关闭本窗口。");
        return 0;
    }

    // ── helpers ─────────────────────────────────────────────────────────────────

    private static void Banner()
    {
        Console.WriteLine("============================================");
        Console.WriteLine("  MARVEL · A股多Agent投研分析");
        Console.WriteLine("============================================");
    }

    private static string ResolveRepoRoot()
    {
        string fromEnv = Environment.GetEnvironmentVariable("MARVEL_HOME");
        if (!string.IsNullOrEmpty(fromEnv) && File.Exists(Path.Combine(fromEnv, @"venv\Scripts\marvel-web.exe")))
        {
            return fromEnv;
        }

        // Compiled-in location first (this is the machine it was built for), then a
        // few places the exe might have been copied to.
        string[] candidates = new string[]
        {
            @"C:\Users\zangk\marvel",
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "marvel"),
            Environment.CurrentDirectory,
        };
        foreach (string candidate in candidates)
        {
            if (string.IsNullOrEmpty(candidate)) continue;
            if (File.Exists(Path.Combine(candidate, @"venv\Scripts\marvel-web.exe")))
            {
                return candidate;
            }
        }

        try
        {
            string exeDir = Path.GetDirectoryName(
                Process.GetCurrentProcess().MainModule.FileName);
            if (!string.IsNullOrEmpty(exeDir))
            {
                string parent = Path.GetDirectoryName(exeDir);
                if (!string.IsNullOrEmpty(parent)
                    && File.Exists(Path.Combine(parent, @"venv\Scripts\marvel-web.exe")))
                {
                    return parent;
                }
            }
        }
        catch (Exception) { /* MainModule can be unavailable; the candidates above cover it */ }

        return null;
    }

    private static Process StartServer(string launcher, string repo)
    {
        try
        {
            ProcessStartInfo info = new ProcessStartInfo(launcher);
            info.WorkingDirectory = repo;
            info.UseShellExecute = false;
            info.CreateNoWindow = true;
            info.RedirectStandardOutput = true;
            info.RedirectStandardError = true;
            // The server (Python/Streamlit) emits UTF-8. Decode it as such, then
            // re-emit in the console's own encoding for display and as UTF-8 in the
            // log, so neither ends up mangled.
            try
            {
                info.StandardOutputEncoding = Encoding.UTF8;
                info.StandardErrorEncoding = Encoding.UTF8;
            }
            catch (Exception) { /* older framework: fall back to the console code page */ }

            Process child = new Process();
            child.StartInfo = info;
            child.OutputDataReceived += delegate(object s, DataReceivedEventArgs e) { Emit(e.Data); };
            child.ErrorDataReceived += delegate(object s, DataReceivedEventArgs e) { Emit(e.Data); };
            child.Start();
            child.BeginOutputReadLine();
            child.BeginErrorReadLine();
            return child;
        }
        catch (Exception ex)
        {
            Console.WriteLine("[MARVEL] " + ex.Message);
            return null;
        }
    }

    private static bool WaitUntilReady(Process child, ref bool cancelled)
    {
        DateTime deadline = DateTime.UtcNow.AddSeconds(ReadyTimeoutSeconds);
        Console.Write("[MARVEL] 等待就绪");
        while (DateTime.UtcNow < deadline)
        {
            if (cancelled) return false;
            if (IsHealthy(1200))
            {
                Console.WriteLine();
                return true;
            }
            if (HasExited(child)) return false;
            Console.Write(".");
            Thread.Sleep(700);
        }
        Console.WriteLine();
        return false;
    }

    /// <summary>Is a healthy Streamlit server answering on the health endpoint?</summary>
    private static bool IsHealthy(int timeoutMs)
    {
        try
        {
            HttpWebRequest request = (HttpWebRequest)WebRequest.Create(HealthUrl);
            request.Timeout = timeoutMs;
            request.Method = "GET";
            // Loopback: never let a (possibly broken) system proxy in the way.
            request.Proxy = null;
            using (HttpWebResponse response = (HttpWebResponse)request.GetResponse())
            {
                return (int)response.StatusCode == 200;
            }
        }
        catch (Exception)
        {
            return false;
        }
    }

    /// <summary>Is anything at all listening on the port?</summary>
    private static bool PortIsOpen()
    {
        try
        {
            using (TcpClient client = new TcpClient())
            {
                IAsyncResult result = client.BeginConnect("127.0.0.1", HealthPort, null, null);
                bool connected = result.AsyncWaitHandle.WaitOne(800, false) && client.Connected;
                try { client.EndConnect(result); } catch (Exception) { }
                return connected;
            }
        }
        catch (Exception)
        {
            return false;
        }
    }

    private static bool HasExited(Process child)
    {
        try { return child.HasExited; }
        catch (Exception) { return true; }
    }

    private static void Stop(Process child)
    {
        try
        {
            if (!child.HasExited)
            {
                child.Kill();
                child.WaitForExit(5000);
            }
        }
        catch (Exception) { /* already gone */ }
    }

    private static void OpenBrowser()
    {
        try
        {
            ProcessStartInfo info = new ProcessStartInfo(AppUrl);
            info.UseShellExecute = true;
            Process.Start(info);
        }
        catch (Exception ex)
        {
            Console.WriteLine("[MARVEL] 无法自动打开浏览器（" + ex.Message + "），请手动访问 " + AppUrl);
        }
    }

    private static void Emit(string line)
    {
        if (line == null) return;
        Console.WriteLine(line);
        Log(line);
    }

    private static void Log(string line)
    {
        if (string.IsNullOrEmpty(_logPath)) return;
        try
        {
            lock (LogLock)
            {
                string dir = Path.GetDirectoryName(_logPath);
                if (!Directory.Exists(dir)) Directory.CreateDirectory(dir);
                File.AppendAllText(_logPath, line + Environment.NewLine, Encoding.UTF8);
            }
        }
        catch (Exception) { /* logging must never break the launcher */ }
    }

    private static void TailLog(int lines)
    {
        if (string.IsNullOrEmpty(_logPath) || !File.Exists(_logPath)) return;
        try
        {
            string[] all = File.ReadAllLines(_logPath);
            int start = Math.Max(0, all.Length - lines);
            Console.WriteLine();
            Console.WriteLine("[MARVEL] 日志最后 " + (all.Length - start) + " 行（完整内容见 " + _logPath + "）:");
            for (int i = start; i < all.Length; i++)
            {
                Console.WriteLine("  | " + all[i]);
            }
        }
        catch (Exception) { }
    }

    private static void Fail(string message)
    {
        Log("[ERROR] " + message);
        Console.WriteLine();
        Console.WriteLine("[ERROR] " + message);
        Pause("按回车关闭本窗口。");
    }

    private static void Pause(string prompt)
    {
        Console.WriteLine();
        Console.Write(prompt);
        try { Console.ReadLine(); }
        catch (Exception) { /* no stdin (double-click on some shells); nothing to wait for */ }
    }
}
