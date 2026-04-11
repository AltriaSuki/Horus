import 'dart:async';

import 'package:flutter/material.dart';

import '../../models/gaze_frame.dart';
import '../../models/subject.dart';
import '../../services/app_state.dart';
import '../../services/engine_client.dart';
import '../../theme/app_colors.dart';

/// Training tab — Phase 1 placeholder for the Unity training game.
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
        sessionId: session.id,
        gameName: 'snake_placeholder',
      );
      if (!mounted) return;
      setState(() =>
          _status = '训练 session 已注册 · ${session.id.substring(0, 8)}…');
    } catch (e) {
      if (!mounted) return;
      setState(() => _status = '启动失败: $e');
    }
  }

  @override
  Widget build(BuildContext context) {
    return SingleChildScrollView(
      padding: const EdgeInsets.fromLTRB(20, 8, 20, 32),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          // ===== Hero ===========================================================
          Container(
            padding: const EdgeInsets.all(24),
            decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(28),
              gradient: const LinearGradient(
                begin: Alignment.topLeft,
                end: Alignment.bottomRight,
                colors: [Color(0xFFCCF3F0), Color(0xFFFFF0C2)],
              ),
            ),
            child: Row(
              children: [
                Container(
                  width: 80,
                  height: 80,
                  decoration: BoxDecoration(
                    color: AppColors.secondary,
                    shape: BoxShape.circle,
                    boxShadow: [
                      BoxShadow(
                        color: AppColors.secondary.withValues(alpha: 0.28),
                        blurRadius: 20,
                        offset: const Offset(0, 8),
                      ),
                    ],
                  ),
                  child: const Icon(Icons.sports_esports_rounded,
                      color: Colors.white, size: 42),
                ),
                const SizedBox(width: 18),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Text('眼控训练游戏',
                          style:
                              Theme.of(context).textTheme.headlineMedium),
                      const SizedBox(height: 4),
                      Text(
                        '用眼睛玩游戏，练练专注力',
                        style: Theme.of(context).textTheme.bodyMedium,
                      ),
                      const SizedBox(height: 6),
                      Container(
                        padding: const EdgeInsets.symmetric(
                            horizontal: 10, vertical: 3),
                        decoration: BoxDecoration(
                          color: AppColors.tertiaryContainer,
                          borderRadius: BorderRadius.circular(12),
                        ),
                        child: Text(
                          '🚧 即将推出',
                          style: Theme.of(context)
                              .textTheme
                              .labelMedium
                              ?.copyWith(
                                color: AppColors.onTertiaryContainer,
                                fontWeight: FontWeight.w700,
                              ),
                        ),
                      ),
                    ],
                  ),
                ),
              ],
            ),
          ),
          const SizedBox(height: 24),

          // ===== Subject + start ===============================================
          Text('选谁来训练？',
              style: Theme.of(context).textTheme.titleLarge),
          const SizedBox(height: 12),
          ListenableBuilder(
            listenable: widget.state,
            builder: (context, _) {
              if (widget.state.loading && widget.state.subjects.isEmpty) {
                return const LinearProgressIndicator();
              }
              final subjects = widget.state.subjects;
              if (subjects.isEmpty) {
                return Container(
                  padding: const EdgeInsets.all(16),
                  decoration: BoxDecoration(
                    color: AppColors.surfaceContainer,
                    borderRadius: BorderRadius.circular(18),
                  ),
                  child: const Row(
                    children: [
                      Icon(Icons.info_outline,
                          color: AppColors.onSurfaceMuted),
                      SizedBox(width: 12),
                      Expanded(child: Text('请先到"被试"标签添加一位小朋友')),
                    ],
                  ),
                );
              }
              if (_selected != null &&
                  !subjects.any((s) => s.id == _selected!.id)) {
                _selected = null;
              }
              return Container(
                padding:
                    const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
                decoration: BoxDecoration(
                  color: AppColors.surfaceContainer,
                  borderRadius: BorderRadius.circular(20),
                  border: Border.all(color: AppColors.divider),
                ),
                child: DropdownButtonHideUnderline(
                  child: DropdownButton<Subject>(
                    isExpanded: true,
                    value: _selected,
                    hint: const Padding(
                      padding: EdgeInsets.symmetric(vertical: 12),
                      child: Text('请选择'),
                    ),
                    items: subjects
                        .map((s) => DropdownMenuItem(
                              value: s,
                              child: Padding(
                                padding: const EdgeInsets.symmetric(
                                    vertical: 8),
                                child:
                                    Text('${s.displayName} (${s.id})'),
                              ),
                            ))
                        .toList(),
                    onChanged: (v) => setState(() => _selected = v),
                  ),
                ),
              );
            },
          ),
          const SizedBox(height: 20),
          FilledButton.icon(
            onPressed: _selected == null ? null : _startTraining,
            icon: const Icon(Icons.play_circle_filled_rounded),
            label: const Text('开始训练 (占位)'),
            style: FilledButton.styleFrom(
              minimumSize: const Size.fromHeight(60),
              backgroundColor: AppColors.secondary,
              foregroundColor: Colors.white,
            ),
          ),
          const SizedBox(height: 8),
          Center(
            child: Text(_status,
                style: Theme.of(context).textTheme.bodySmall),
          ),
          const SizedBox(height: 28),

          // ===== Live gaze monitor ============================================
          Text('实时注视监控',
              style: Theme.of(context).textTheme.titleLarge),
          const SizedBox(height: 4),
          Text('早筛运行时能看到小朋友的注视轨迹',
              style: Theme.of(context).textTheme.bodySmall),
          const SizedBox(height: 12),
          Container(
            height: 220,
            decoration: BoxDecoration(
              color: AppColors.onSurface,
              borderRadius: BorderRadius.circular(22),
            ),
            clipBehavior: Clip.antiAlias,
            child: LayoutBuilder(builder: (context, constraints) {
              final f = _lastFrame;
              if (f == null) {
                return const Center(
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Icon(Icons.visibility_off_outlined,
                          size: 32, color: Colors.white38),
                      SizedBox(height: 8),
                      Text('等待 gaze 数据…',
                          style: TextStyle(color: Colors.white54)),
                    ],
                  ),
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
                    left: vx - 14,
                    top: vy - 14,
                    child: Container(
                      width: 28,
                      height: 28,
                      decoration: BoxDecoration(
                        color: AppColors.tertiary,
                        shape: BoxShape.circle,
                        boxShadow: [
                          BoxShadow(
                            color:
                                AppColors.tertiary.withValues(alpha: 0.5),
                            blurRadius: 12,
                            spreadRadius: 2,
                          ),
                        ],
                      ),
                    ),
                  ),
                  Positioned(
                    left: 12, top: 12,
                    child: Container(
                      padding: const EdgeInsets.symmetric(
                          horizontal: 10, vertical: 6),
                      decoration: BoxDecoration(
                        color: Colors.white10,
                        borderRadius: BorderRadius.circular(10),
                      ),
                      child: Text(
                        'x=${f.x.toStringAsFixed(0)}  y=${f.y.toStringAsFixed(0)}\n'
                        'pupil=${f.pupil.toStringAsFixed(3)}  ${f.fps} fps',
                        style: const TextStyle(
                          color: Colors.white70,
                          fontFamily: 'monospace',
                          fontSize: 11,
                          height: 1.4,
                        ),
                      ),
                    ),
                  ),
                ],
              );
            }),
          ),
        ],
      ),
    );
  }
}
