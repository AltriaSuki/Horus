import 'dart:async';
import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../../models/task_event.dart';
import '../../services/engine_client.dart';
import '../../theme/app_colors.dart';
import 'result_page.dart';

/// Live progress while a worker subprocess runs the Sternberg task.
///
/// Subscribes to ``WS /events`` and updates a large circular progress ring
/// on each ``trial_end``. When ``report_ready`` arrives we navigate to the
/// result page.
///
/// Visual design goals:
///   * Big central progress ring — the child feels "I've almost finished"
///   * Encouraging copy that rotates ("做得真棒!", "马上完成啦!", "太厉害了!")
///   * Pulsing mascot during active phases so it doesn't look frozen
///   * Gentle cancel button (never scary) but clear label
class ScreeningRunningPage extends StatefulWidget {
  final EngineClient engine;
  final String sessionId;
  final String subjectName;

  const ScreeningRunningPage({
    super.key,
    required this.engine,
    required this.sessionId,
    required this.subjectName,
  });

  @override
  State<ScreeningRunningPage> createState() => _ScreeningRunningPageState();
}

class _ScreeningRunningPageState extends State<ScreeningRunningPage>
    with SingleTickerProviderStateMixin {
  StreamSubscription<TaskEvent>? _sub;

  String _phase = '正在准备小游戏…';
  String _subPhase = '加载模型 · 打开摄像头 · 预加载图片\n(大约 5-10 秒)';
  int _completedTrials = 0;
  int _totalTrials = 160;
  int _correctCount = 0;
  int _currentBlock = 0;
  int _totalBlocks = 8;
  String? _error;
  bool _navigated = false;

  late final AnimationController _pulseController;

  static const List<String> _encouragements = [
    '做得真棒！',
    '继续加油！',
    '眼睛跟着圆点走～',
    '太厉害啦！',
    '马上完成啦！',
    '你真专注！',
  ];

  @override
  void initState() {
    super.initState();
    _pulseController = AnimationController(
      vsync: this,
      duration: const Duration(seconds: 2),
    )..repeat(reverse: true);
    _subscribeEvents();
  }

  void _subscribeEvents() {
    _sub = widget.engine.eventStream().listen(
      _handle,
      onError: (e) {
        if (!mounted) return;
        setState(() => _error = '事件流错误: $e');
      },
    );
  }

  void _handle(TaskEvent ev) {
    if (!mounted) return;
    if (ev.sessionId != null && ev.sessionId != widget.sessionId) return;

    setState(() {
      switch (ev.type) {
        case 'session_running':
          _phase = '准备就绪！';
          _subPhase = '工作进程已启动，马上开始小游戏';
        case 'calibration_start':
          _phase = '先做个小练习';
          _subPhase = '按屏幕提示点击 13 个蓝色圆圈';
        case 'calibration_done':
          _phase = '练习完成！';
          _subPhase = '马上开始正式挑战';
        case 'task_start':
          _totalTrials = ev.totalTrials ?? 160;
          _totalBlocks = (ev.payload['n_blocks'] as int?) ?? 8;
          _phase = '视觉记忆挑战开始！';
          _subPhase = '记住圆点出现过的位置';
        case 'block_start':
          _currentBlock = (ev.payload['block'] as int?) ?? _currentBlock;
          _phase = '第 $_currentBlock 关开始';
          _subPhase = _randomEncouragement();
        case 'trial_end':
          _completedTrials += 1;
          if ((ev.payload['correct'] as int? ?? 0) > 0) _correctCount += 1;
          if (_completedTrials % 5 == 0) {
            _subPhase = _randomEncouragement();
          }
        case 'block_end':
          _phase = '第 $_currentBlock 关完成！';
          _subPhase = '休息一下，再接再厉 🌟';
        case 'task_done':
          _phase = '全部完成啦！';
          _subPhase = '正在生成报告…';
        case 'report_ready':
          _phase = '报告已生成 🎉';
          _gotoResult();
        case 'error':
          _error = '${ev.payload["code"]}: ${ev.payload["message"]}';
      }
    });
  }

  String _randomEncouragement() {
    final idx = DateTime.now().millisecond % _encouragements.length;
    return _encouragements[idx];
  }

  Future<void> _gotoResult() async {
    if (_navigated) return;
    _navigated = true;
    await Future<void>.delayed(const Duration(milliseconds: 800));
    if (!mounted) return;
    Navigator.of(context).pushReplacement(MaterialPageRoute(
      builder: (_) => ScreeningResultPage(
        engine: widget.engine,
        sessionId: widget.sessionId,
        subjectName: widget.subjectName,
      ),
    ));
  }

  Future<void> _cancel() async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (_) => AlertDialog(
        title: const Text('确认退出'),
        content: const Text('退出后本次挑战就不会生成报告哦。确定要退出吗？'),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(false),
            child: const Text('继续挑战'),
          ),
          FilledButton.tonal(
            onPressed: () => Navigator.of(context).pop(true),
            child: const Text('退出'),
          ),
        ],
      ),
    );
    if (ok != true) return;
    try {
      await widget.engine.cancelSession(widget.sessionId);
    } catch (_) {}
    if (mounted) Navigator.of(context).pop();
  }

  @override
  void dispose() {
    _sub?.cancel();
    _pulseController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final progress = _totalTrials == 0
        ? 0.0
        : (_completedTrials / _totalTrials).clamp(0.0, 1.0);
    final accuracy = _completedTrials == 0
        ? 0.0
        : _correctCount / _completedTrials;
    final pctInt = (progress * 100).round();

    return Scaffold(
      appBar: AppBar(
        title: Text(widget.subjectName),
        leading: IconButton(
          icon: const Icon(Icons.close_rounded),
          onPressed: _cancel,
          tooltip: '退出',
        ),
      ),
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 32, vertical: 16),
          child: Column(
            children: [
              const SizedBox(height: 16),

              // ===== Phase banner ============================================
              Text(
                _phase,
                textAlign: TextAlign.center,
                style: Theme.of(context).textTheme.headlineMedium,
              ),
              const SizedBox(height: 8),
              Text(
                _subPhase,
                textAlign: TextAlign.center,
                style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                      color: AppColors.onSurfaceMuted,
                    ),
              ),

              const SizedBox(height: 32),

              // ===== Big progress ring ======================================
              Expanded(
                child: Center(
                  child: AnimatedBuilder(
                    animation: _pulseController,
                    builder: (context, _) {
                      final pulseScale = 1 + 0.03 * _pulseController.value;
                      return Transform.scale(
                        scale: pulseScale,
                        child: SizedBox(
                          width: 260,
                          height: 260,
                          child: Stack(
                            alignment: Alignment.center,
                            children: [
                              // Outer track
                              CustomPaint(
                                size: const Size(260, 260),
                                painter: _RingPainter(
                                  progress: 1.0,
                                  color: AppColors.surfaceContainerHigh,
                                  strokeWidth: 22,
                                ),
                              ),
                              // Active progress
                              CustomPaint(
                                size: const Size(260, 260),
                                painter: _RingPainter(
                                  progress: progress,
                                  color: AppColors.primary,
                                  strokeWidth: 22,
                                  rounded: true,
                                ),
                              ),
                              // Center content
                              Column(
                                mainAxisSize: MainAxisSize.min,
                                children: [
                                  Text('$pctInt%',
                                      style: const TextStyle(
                                        fontSize: 56,
                                        fontWeight: FontWeight.w800,
                                        color: AppColors.primary,
                                      )),
                                  const SizedBox(height: 2),
                                  Text('$_completedTrials / $_totalTrials',
                                      style: Theme.of(context)
                                          .textTheme
                                          .titleMedium
                                          ?.copyWith(
                                            color: AppColors.onSurfaceMuted,
                                          )),
                                ],
                              ),
                            ],
                          ),
                        ),
                      );
                    },
                  ),
                ),
              ),

              // ===== Stats row ==============================================
              Row(
                children: [
                  Expanded(
                    child: _StatChip(
                      icon: Icons.stars_rounded,
                      color: AppColors.tertiary,
                      label: '准确率',
                      value: '${(accuracy * 100).round()}%',
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: _StatChip(
                      icon: Icons.flag_rounded,
                      color: AppColors.secondary,
                      label: '当前关',
                      value: _currentBlock == 0
                          ? '—'
                          : '$_currentBlock / $_totalBlocks',
                    ),
                  ),
                ],
              ),

              if (_error != null) ...[
                const SizedBox(height: 20),
                Container(
                  padding: const EdgeInsets.all(16),
                  decoration: BoxDecoration(
                    color: AppColors.errorContainer,
                    borderRadius: BorderRadius.circular(16),
                  ),
                  child: Row(
                    children: [
                      const Icon(Icons.error_outline,
                          color: AppColors.error),
                      const SizedBox(width: 12),
                      Expanded(
                        child: Text(
                          _error!,
                          style: const TextStyle(
                            color: AppColors.onErrorContainer,
                            fontSize: 14,
                          ),
                        ),
                      ),
                    ],
                  ),
                ),
              ],
              const SizedBox(height: 16),
            ],
          ),
        ),
      ),
    );
  }
}


// ───────────────────────────────────────────────────────────────────────────
// Custom ring painter — fat, rounded progress arc
// ───────────────────────────────────────────────────────────────────────────


class _RingPainter extends CustomPainter {
  final double progress;
  final Color color;
  final double strokeWidth;
  final bool rounded;

  _RingPainter({
    required this.progress,
    required this.color,
    required this.strokeWidth,
    this.rounded = false,
  });

  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()
      ..color = color
      ..style = PaintingStyle.stroke
      ..strokeWidth = strokeWidth
      ..strokeCap = rounded ? StrokeCap.round : StrokeCap.butt;
    final rect = Rect.fromLTWH(
      strokeWidth / 2,
      strokeWidth / 2,
      size.width - strokeWidth,
      size.height - strokeWidth,
    );
    canvas.drawArc(
      rect,
      -math.pi / 2,
      progress * 2 * math.pi,
      false,
      paint,
    );
  }

  @override
  bool shouldRepaint(covariant _RingPainter old) =>
      old.progress != progress || old.color != color;
}


// ───────────────────────────────────────────────────────────────────────────
// Small stat chip at the bottom
// ───────────────────────────────────────────────────────────────────────────


class _StatChip extends StatelessWidget {
  final IconData icon;
  final Color color;
  final String label;
  final String value;

  const _StatChip({
    required this.icon,
    required this.color,
    required this.label,
    required this.value,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: AppColors.divider),
      ),
      child: Row(
        children: [
          Container(
            width: 44,
            height: 44,
            decoration: BoxDecoration(
              color: color.withValues(alpha: 0.18),
              borderRadius: BorderRadius.circular(14),
            ),
            child: Icon(icon, color: color, size: 24),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: [
                Text(label,
                    style: Theme.of(context).textTheme.labelMedium),
                Text(value,
                    style: Theme.of(context).textTheme.titleMedium),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
