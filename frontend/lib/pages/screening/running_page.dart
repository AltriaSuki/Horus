import 'dart:async';

import 'package:flutter/material.dart';

import '../../models/task_event.dart';
import '../../services/engine_client.dart';
import 'result_page.dart';

/// Live progress while a worker subprocess runs the Sternberg task.
///
/// Subscribes to ``WS /events`` and updates a progress indicator on each
/// trial_end. When ``report_ready`` arrives we navigate to the result page.
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

class _ScreeningRunningPageState extends State<ScreeningRunningPage> {
  StreamSubscription<TaskEvent>? _sub;
  String _phase = '正在启动…';
  int _completedTrials = 0;
  int _totalTrials = 160;
  int _correctCount = 0;
  String? _error;
  bool _navigated = false;

  @override
  void initState() {
    super.initState();
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
          _phase = '工作进程已就绪 (PID ${ev.payload["pid"]})';
        case 'calibration_start':
          _phase = '13 点校准中…请按提示点击屏幕';
        case 'calibration_done':
          _phase = '校准完成';
        case 'task_start':
          _totalTrials = ev.totalTrials ?? 160;
          _phase = 'Sternberg 任务进行中';
        case 'block_start':
          _phase = '区组 ${ev.payload["block"]} 开始';
        case 'trial_end':
          _completedTrials += 1;
          if ((ev.payload['correct'] as int? ?? 0) > 0) _correctCount += 1;
        case 'block_end':
          _phase = '区组 ${ev.payload["block"]} 结束';
        case 'task_done':
          _phase = '任务完成，等待报告生成…';
        case 'report_ready':
          _phase = '报告就绪';
          _gotoResult();
        case 'error':
          _error = '${ev.payload["code"]}: ${ev.payload["message"]}';
      }
    });
  }

  Future<void> _gotoResult() async {
    if (_navigated) return;
    _navigated = true;
    // Small delay so the UI renders the "complete" state briefly
    await Future<void>.delayed(const Duration(milliseconds: 600));
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
        title: const Text('确认中止'),
        content: const Text('中止后本次会话将不会生成报告'),
        actions: [
          TextButton(
              onPressed: () => Navigator.of(context).pop(false),
              child: const Text('继续')),
          FilledButton.tonal(
              onPressed: () => Navigator.of(context).pop(true),
              child: const Text('中止')),
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
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final progress =
        _totalTrials == 0 ? 0.0 : _completedTrials / _totalTrials;
    final accuracy = _completedTrials == 0
        ? 0.0
        : _correctCount / _completedTrials;

    return Scaffold(
      appBar: AppBar(
        title: Text('早筛中 — ${widget.subjectName}'),
        leading: IconButton(
          icon: const Icon(Icons.close),
          onPressed: _cancel,
        ),
      ),
      body: Padding(
        padding: const EdgeInsets.all(32),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(_phase,
                style: Theme.of(context).textTheme.headlineSmall),
            const SizedBox(height: 32),
            LinearProgressIndicator(
              value: progress.clamp(0.0, 1.0),
              minHeight: 12,
              borderRadius: BorderRadius.circular(6),
            ),
            const SizedBox(height: 8),
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Text('试次 $_completedTrials / $_totalTrials'),
                Text('准确率 ${(accuracy * 100).toStringAsFixed(0)}%'),
              ],
            ),
            const SizedBox(height: 32),
            if (_error != null)
              Card(
                color: Theme.of(context).colorScheme.errorContainer,
                child: Padding(
                  padding: const EdgeInsets.all(16),
                  child: Text(_error!),
                ),
              )
            else
              const Card(
                child: Padding(
                  padding: EdgeInsets.all(16),
                  child: Row(
                    children: [
                      Icon(Icons.info_outline),
                      SizedBox(width: 12),
                      Expanded(
                        child: Text(
                          '请保持注视屏幕并按要求按 F / J 键作答。\n'
                          '中途请勿移动头部或离开摄像头视野。',
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            const Spacer(),
            OutlinedButton.icon(
              onPressed: _cancel,
              icon: const Icon(Icons.stop),
              label: const Text('中止会话'),
            ),
          ],
        ),
      ),
    );
  }
}
