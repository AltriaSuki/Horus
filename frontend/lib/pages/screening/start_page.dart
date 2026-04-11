import 'package:flutter/material.dart';

import '../../models/subject.dart';
import '../../services/app_state.dart';
import '../../services/engine_client.dart';
import 'running_page.dart';

class ScreeningStartPage extends StatefulWidget {
  final EngineClient engine;
  final AppState state;
  const ScreeningStartPage({
    super.key,
    required this.engine,
    required this.state,
  });

  @override
  State<ScreeningStartPage> createState() => _ScreeningStartPageState();
}

class _ScreeningStartPageState extends State<ScreeningStartPage> {
  Subject? _selected;

  Future<void> _start() async {
    final subj = _selected;
    if (subj == null) return;
    try {
      final session = await widget.engine.startSession(
        subjectId: subj.id,
        kind: 'screening',
        mode: 'sternberg',
      );
      if (!mounted) return;
      Navigator.of(context).push(MaterialPageRoute(
        builder: (_) => ScreeningRunningPage(
          engine: widget.engine,
          sessionId: session.id,
          subjectName: subj.displayName,
        ),
      ));
    } catch (e) {
      if (!mounted) return;
      showDialog(
        context: context,
        builder: (_) => AlertDialog(
          title: const Text('启动失败'),
          content: Text(e.toString()),
          actions: [
            TextButton(
              onPressed: () => Navigator.of(context).pop(),
              child: const Text('好'),
            ),
          ],
        ),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.all(24),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text('Sternberg 视空间工作记忆任务',
              style: Theme.of(context).textTheme.headlineSmall),
          const SizedBox(height: 8),
          const Text(
            '完整流程包括 13 点点击校准 + 8 个 block × 20 trial '
            '(共 160 trial)，约 25 分钟。完成后系统会输出 ADHD '
            '风险概率与解释性特征。',
          ),
          const SizedBox(height: 24),
          ListenableBuilder(
            listenable: widget.state,
            builder: (context, _) {
              if (widget.state.loading && widget.state.subjects.isEmpty) {
                return const Center(child: CircularProgressIndicator());
              }
              if (widget.state.error != null && widget.state.subjects.isEmpty) {
                return Text('加载被试失败: ${widget.state.error}');
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
              // Drop the cached selection if the underlying list changed.
              if (_selected != null &&
                  !subjects.any((s) => s.id == _selected!.id)) {
                _selected = null;
              }
              return DropdownButtonFormField<Subject>(
                initialValue: _selected,
                decoration: const InputDecoration(
                  labelText: '选择被试',
                  border: OutlineInputBorder(),
                ),
                items: subjects
                    .map((s) => DropdownMenuItem(
                          value: s,
                          child: Text('${s.displayName}  (${s.id})'),
                        ))
                    .toList(),
                onChanged: (v) => setState(() => _selected = v),
              );
            },
          ),
          const SizedBox(height: 32),
          FilledButton.icon(
            onPressed: _selected == null ? null : _start,
            icon: const Icon(Icons.play_arrow),
            label: const Padding(
              padding: EdgeInsets.symmetric(vertical: 12, horizontal: 16),
              child: Text('开始早筛', style: TextStyle(fontSize: 16)),
            ),
          ),
          const SizedBox(height: 24),
          Card(
            color: Theme.of(context).colorScheme.surfaceContainerHighest,
            child: const Padding(
              padding: EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('注意事项', style: TextStyle(fontWeight: FontWeight.bold)),
                  SizedBox(height: 8),
                  Text('• 任务期间会进入全屏模式 (子进程)，前端窗口仍可显示进度'),
                  Text('• 摄像头第一次启动可能需要授予权限'),
                  Text('• 中途按 ESC 可中止'),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }
}
