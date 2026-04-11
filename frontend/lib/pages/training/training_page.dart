import 'dart:async';

import 'package:flutter/material.dart';

import '../../models/gaze_frame.dart';
import '../../models/subject.dart';
import '../../services/app_state.dart';
import '../../services/engine_client.dart';

/// Training tab — Phase 1 placeholder for the Unity training game.
///
/// Shows:
///   * A subject picker + a "start training" button (creates a `kind=game`
///     session — Phase 2 doesn't actually launch Unity, just registers).
///   * A live "gaze cursor" that follows the latest GazeFrame from `WS /gaze`.
///     Sanity check that the engine ↔ frontend stream works end-to-end.
class TrainingPage extends StatefulWidget {
  final EngineClient engine;
  final AppState state;
  const TrainingPage({
    super.key,
    required this.engine,
    required this.state,
  });

  @override
  State<TrainingPage> createState() => _TrainingPageState();
}

class _TrainingPageState extends State<TrainingPage> {
  Subject? _selected;
  StreamSubscription<GazeFrame>? _gazeSub;
  GazeFrame? _lastFrame;
  String _status = '未开始';

  @override
  void initState() {
    super.initState();
    _subscribeGaze();
  }

  void _subscribeGaze() {
    _gazeSub?.cancel();
    _gazeSub = widget.engine.gazeStream().listen(
      (frame) {
        if (mounted) setState(() => _lastFrame = frame);
      },
      onError: (e) {
        if (mounted) setState(() => _status = 'Gaze 流断开: $e');
      },
    );
  }

  @override
  void dispose() {
    _gazeSub?.cancel();
    super.dispose();
  }

  Future<void> _startTraining() async {
    final subj = _selected;
    if (subj == null) return;
    try {
      final session = await widget.engine.startSession(
        subjectId: subj.id,
        kind: 'game',
        mode: 'game_snake',
      );
      await widget.engine.createGameRun(
        sessionId: session.id, gameName: 'snake_placeholder',
      );
      if (!mounted) return;
      setState(() => _status = '已注册训练 session ${session.id.substring(0, 8)}…');
    } catch (e) {
      if (!mounted) return;
      setState(() => _status = '启动失败: $e');
    }
  }

  @override
  Widget build(BuildContext context) {
    return SingleChildScrollView(
      padding: const EdgeInsets.all(24),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text('眼控训练 (Unity 占位)',
              style: Theme.of(context).textTheme.headlineSmall),
          const SizedBox(height: 8),
          const Text(
            '此页面是 Phase 1 的占位符 — Unity 训练游戏会在 Phase 2/3 接入。'
            '当前可以验证：(1) 创建一个 kind=game session；(2) 实时 gaze 流可订阅。',
          ),
          const SizedBox(height: 24),

          ListenableBuilder(
            listenable: widget.state,
            builder: (context, _) {
              if (widget.state.loading && widget.state.subjects.isEmpty) {
                return const LinearProgressIndicator();
              }
              final subjects = widget.state.subjects;
              if (subjects.isEmpty) {
                return const Card(
                  child: ListTile(
                    leading: Icon(Icons.info_outline),
                    title: Text('请先在"被试"标签创建一个被试'),
                  ),
                );
              }
              if (_selected != null &&
                  !subjects.any((s) => s.id == _selected!.id)) {
                _selected = null;
              }
              return Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  DropdownButtonFormField<Subject>(
                    initialValue: _selected,
                    decoration: const InputDecoration(
                      labelText: '选择被试',
                      border: OutlineInputBorder(),
                    ),
                    items: subjects
                        .map((s) => DropdownMenuItem(
                              value: s,
                              child: Text('${s.displayName} (${s.id})'),
                            ))
                        .toList(),
                    onChanged: (v) => setState(() => _selected = v),
                  ),
                  const SizedBox(height: 16),
                  FilledButton.icon(
                    onPressed: _selected == null ? null : _startTraining,
                    icon: const Icon(Icons.sports_esports),
                    label: const Text('开始训练 (注册 session)'),
                  ),
                  const SizedBox(height: 8),
                  Text(_status,
                      style: Theme.of(context).textTheme.bodySmall),
                ],
              );
            },
          ),

          const SizedBox(height: 32),

          // ====== Live gaze indicator =========================================
          Text('实时 Gaze 流监控',
              style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 8),
          Container(
            height: 240,
            decoration: BoxDecoration(
              color: Colors.black87,
              borderRadius: BorderRadius.circular(8),
            ),
            clipBehavior: Clip.antiAlias,
            child: LayoutBuilder(builder: (context, constraints) {
              final f = _lastFrame;
              if (f == null) {
                return const Center(
                  child: Text('等待 gaze 帧… (启动一个 session 会有数据)',
                      style: TextStyle(color: Colors.white54)),
                );
              }
              if (!f.valid) {
                return const Center(
                  child: Text('眨眼或检测失败',
                      style: TextStyle(color: Colors.amber)),
                );
              }
              final vx = (f.x / 1920.0) * constraints.maxWidth;
              final vy = (f.y / 1080.0) * constraints.maxHeight;
              return Stack(
                children: [
                  Positioned(
                    left: vx - 12,
                    top: vy - 12,
                    child: Container(
                      width: 24,
                      height: 24,
                      decoration: const BoxDecoration(
                        color: Colors.greenAccent,
                        shape: BoxShape.circle,
                      ),
                    ),
                  ),
                  Positioned(
                    left: 8, top: 8,
                    child: Text(
                      'x=${f.x.toStringAsFixed(0)}  y=${f.y.toStringAsFixed(0)}\n'
                      'pupil=${f.pupil.toStringAsFixed(3)}  fps=${f.fps}',
                      style: const TextStyle(
                        color: Colors.white70, fontFamily: 'monospace',
                        fontSize: 11,
                      ),
                    ),
                  ),
                ],
              );
            }),
          ),

          const SizedBox(height: 24),
          Card(
            color: Theme.of(context).colorScheme.surfaceContainerHighest,
            child: const Padding(
              padding: EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('Unity 接入约定 (供后续开发参考)',
                      style: TextStyle(fontWeight: FontWeight.bold)),
                  SizedBox(height: 8),
                  Text('• Unity 客户端连接 ws://127.0.0.1:8765/gaze 即可订阅同一根 gaze 流'),
                  Text('• 9 宫格映射：把屏幕分成 9 块，落在哪格 = 哪个虚拟方向键'),
                  Text('• 游戏分数通过 PATCH /games/runs/{id} 回写引擎'),
                  Text('• 详细协议见 engine/docs/ws_protocol.md'),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }
}
