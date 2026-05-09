using System;
using System.IO;
using System.Net.Sockets;
using System.Threading;
using UnityEngine;

/// <summary>
/// 眼动追踪客户端 - 连接 eye_tracker_server.py 的 TCP 服务器
/// 
/// 用法:
///   1. 将此脚本挂到任意 GameObject 上
///   2. 先启动 Python 服务端: python eye_tracker_server.py --port 5678
///   3. 运行 Unity, 脚本会自动连接并接收注视方向
///   
/// 外部读取:
///   EyeTrackerClient client = FindObjectOfType<EyeTrackerClient>();
///   string zone = client.GazeZone;        // "up"/"down"/"left"/"right"/"center"
///   Vector2 gaze = client.GazePosition;   // 归一化屏幕坐标 (0~1, 0~1)
///   bool connected = client.IsConnected;
/// </summary>
public class EyeTrackerClient : MonoBehaviour
{
    [Header("Server Settings")]
    [Tooltip("Python 服务端 IP 地址")]
    public string serverHost = "127.0.0.1";

    [Tooltip("Python 服务端端口")]
    public int serverPort = 5678;

    [Tooltip("断线后自动重连间隔 (秒)")]
    public float reconnectInterval = 3f;

    // ---- 公开属性 (线程安全) ----

    /// <summary>当前注视方向: "up" / "down" / "left" / "right" / "center"</summary>
    public string GazeZone { get; private set; } = "center";

    /// <summary>归一化屏幕坐标 (0~1)</summary>
    public Vector2 GazePosition { get; private set; } = new Vector2(0.5f, 0.5f);

    /// <summary>服务端时间戳</summary>
    public double Timestamp { get; private set; }

    /// <summary>是否已连接</summary>
    public bool IsConnected { get; private set; }

    // ---- 事件 (主线程) ----

    /// <summary>方向变化时触发 (oldZone, newZone)</summary>
    public event Action<string, string> OnZoneChanged;

    // ---- 内部 ----
    private TcpClient _tcp;
    private Thread _thread;
    private volatile bool _running;
    private readonly object _lock = new object();

    private string _pendingZone;
    private float _pendingGazeX, _pendingGazeY;
    private double _pendingTs;
    private bool _hasNewData;
    private string _lastZone = "center";

    void Start()
    {
        _running = true;
        _thread = new Thread(ReceiveLoop)
        {
            IsBackground = true,
            Name = "EyeTrackerReceiver"
        };
        _thread.Start();
    }

    void Update()
    {
        // 在主线程中消费数据
        lock (_lock)
        {
            if (!_hasNewData) return;
            GazeZone = _pendingZone;
            GazePosition = new Vector2(_pendingGazeX, _pendingGazeY);
            Timestamp = _pendingTs;
            _hasNewData = false;
        }

        if (GazeZone != _lastZone)
        {
            OnZoneChanged?.Invoke(_lastZone, GazeZone);
            _lastZone = GazeZone;
        }
    }

    void OnDestroy()
    {
        _running = false;
        try { _tcp?.Close(); } catch { }
        _thread?.Join(1000);
    }

    void OnApplicationQuit()
    {
        _running = false;
        try { _tcp?.Close(); } catch { }
    }

    // ---- 接收线程 ----

    private void ReceiveLoop()
    {
        while (_running)
        {
            try
            {
                Debug.Log($"[EyeTracker] Connecting to {serverHost}:{serverPort}...");
                _tcp = new TcpClient();
                _tcp.Connect(serverHost, serverPort);
                _tcp.NoDelay = true;
                IsConnected = true;
                Debug.Log("[EyeTracker] Connected.");

                using (var reader = new StreamReader(_tcp.GetStream()))
                {
                    while (_running && _tcp.Connected)
                    {
                        string line = reader.ReadLine();
                        if (line == null) break; // 服务端断开

                        ParseLine(line);
                    }
                }
            }
            catch (Exception ex)
            {
                if (_running)
                    Debug.LogWarning($"[EyeTracker] Connection error: {ex.Message}");
            }
            finally
            {
                IsConnected = false;
                try { _tcp?.Close(); } catch { }
            }

            // 重连等待
            if (_running)
            {
                Debug.Log($"[EyeTracker] Reconnecting in {reconnectInterval}s...");
                Thread.Sleep((int)(reconnectInterval * 1000));
            }
        }
    }

    private void ParseLine(string json)
    {
        // 简易 JSON 解析, 避免依赖 JsonUtility 的类定义
        // 格式: {"zone":"center","gaze_x":0.5,"gaze_y":0.5,"ts":1712345678.123}
        try
        {
            string zone = ExtractString(json, "zone");
            float gx = ExtractFloat(json, "gaze_x");
            float gy = ExtractFloat(json, "gaze_y");
            double ts = ExtractDouble(json, "ts");

            lock (_lock)
            {
                _pendingZone = zone;
                _pendingGazeX = gx;
                _pendingGazeY = gy;
                _pendingTs = ts;
                _hasNewData = true;
            }
        }
        catch (Exception ex)
        {
            Debug.LogWarning($"[EyeTracker] Parse error: {ex.Message}");
        }
    }

    // ---- 简易 JSON 字段提取 ----

    private static string ExtractString(string json, string key)
    {
        string pattern = $"\"{key}\":\"";
        int start = json.IndexOf(pattern, StringComparison.Ordinal);
        if (start < 0) return "";
        start += pattern.Length;
        int end = json.IndexOf('"', start);
        return end < 0 ? "" : json.Substring(start, end - start);
    }

    private static float ExtractFloat(string json, string key)
    {
        string val = ExtractNumber(json, key);
        return float.TryParse(val, System.Globalization.NumberStyles.Float,
            System.Globalization.CultureInfo.InvariantCulture, out float f) ? f : 0f;
    }

    private static double ExtractDouble(string json, string key)
    {
        string val = ExtractNumber(json, key);
        return double.TryParse(val, System.Globalization.NumberStyles.Float,
            System.Globalization.CultureInfo.InvariantCulture, out double d) ? d : 0.0;
    }

    private static string ExtractNumber(string json, string key)
    {
        string pattern = $"\"{key}\":";
        int start = json.IndexOf(pattern, StringComparison.Ordinal);
        if (start < 0) return "0";
        start += pattern.Length;
        int end = start;
        while (end < json.Length && (char.IsDigit(json[end]) || json[end] == '.' || json[end] == '-'))
            end++;
        return json.Substring(start, end - start);
    }
}
