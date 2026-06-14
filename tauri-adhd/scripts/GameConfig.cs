using UnityEngine;
using System.IO;

/// <summary>
/// 游戏难度配置 — 从 game_config.json 读取前端设置的参数。
///
/// 用法:
///   1. 将此脚本放到 Unity 项目的 Scripts 目录
///   2. 在游戏启动时（如 Awake 或 Start）调用 GameConfig.Load()
///   3. 通过 GameConfig.Data 获取各参数值
///
/// 示例:
///   GameConfig.Load();
///   float speed = GameConfig.Data.snakeSpeed;
///   Debug.Log($"蛇速: {speed}, 毒苹果频率: {GameConfig.Data.poisonAppleRate}");
/// </summary>
public static class GameConfig
{
    /// <summary>当前加载的配置数据</summary>
    public static GameConfigData Data { get; private set; }

    /// <summary>
    /// 从 game_config.json 加载配置。
    /// 文件不存在则使用默认值。
    /// </summary>
    public static void Load()
    {
        // game_config.json 位于 eye.exe 同目录
        string path = Path.Combine(Application.dataPath, "..", "game_config.json");

        if (File.Exists(path))
        {
            try
            {
                string json = File.ReadAllText(path);
                Data = JsonUtility.FromJson<GameConfigData>(json);
                Debug.Log($"[GameConfig] 已加载配置: 蛇速={Data.snakeSpeed}, 毒苹果频率={Data.poisonAppleRate}, 炸弹频率={Data.bombRate}, 时长={Data.gameDuration}min");
            }
            catch (System.Exception ex)
            {
                Debug.LogWarning($"[GameConfig] 解析配置文件失败，使用默认值: {ex.Message}");
                Data = new GameConfigData();
            }
        }
        else
        {
            Debug.Log("[GameConfig] 未找到 game_config.json，使用默认值");
            Data = new GameConfigData();
        }
    }
}

/// <summary>
/// 游戏难度配置数据结构。
/// 字段名必须与前端 game_config.json 中的 key 一致。
/// </summary>
[System.Serializable]
public class GameConfigData
{
    /// <summary>蛇的移动速度 (1-10, 默认 5)</summary>
    public float snakeSpeed = 5f;

    /// <summary>毒苹果生成频率 (1-10, 默认 3)</summary>
    public float poisonAppleRate = 3f;

    /// <summary>炸弹生成频率 (1-10, 默认 3)</summary>
    public float bombRate = 3f;

    /// <summary>游戏时长（分钟, 默认 5）</summary>
    public int gameDuration = 5;
}
