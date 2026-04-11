import 'package:flutter/material.dart';

import '../../models/subject.dart';
import '../../services/app_state.dart';

class SubjectCreateDialog extends StatefulWidget {
  final AppState state;
  const SubjectCreateDialog({super.key, required this.state});

  @override
  State<SubjectCreateDialog> createState() => _SubjectCreateDialogState();
}

class _SubjectCreateDialogState extends State<SubjectCreateDialog> {
  final _formKey = GlobalKey<FormState>();
  final _idCtrl = TextEditingController();
  final _nameCtrl = TextEditingController();
  String? _sex;
  bool _busy = false;
  String? _error;

  @override
  void dispose() {
    _idCtrl.dispose();
    _nameCtrl.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (!_formKey.currentState!.validate()) return;
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      // Goes through AppState so every other tab refreshes too.
      final created = await widget.state.createSubject(Subject(
        id: _idCtrl.text.trim(),
        displayName: _nameCtrl.text.trim(),
        sex: _sex,
      ));
      if (!mounted) return;
      Navigator.of(context).pop(created);
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _busy = false;
        _error = e.toString();
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: const Text('添加被试'),
      content: Form(
        key: _formKey,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            TextFormField(
              controller: _idCtrl,
              decoration: const InputDecoration(
                labelText: '被试编号 (例: CHILD_001)',
              ),
              validator: (v) {
                if (v == null || v.trim().isEmpty) return '必填';
                if (!RegExp(r'^[A-Za-z0-9_-]+$').hasMatch(v.trim())) {
                  return '只能含字母 / 数字 / _ -';
                }
                return null;
              },
            ),
            const SizedBox(height: 12),
            TextFormField(
              controller: _nameCtrl,
              decoration: const InputDecoration(labelText: '昵称'),
              validator: (v) =>
                  v == null || v.trim().isEmpty ? '必填' : null,
            ),
            const SizedBox(height: 12),
            DropdownButtonFormField<String?>(
              initialValue: _sex,
              decoration: const InputDecoration(labelText: '性别 (可选)'),
              items: const [
                DropdownMenuItem(value: null, child: Text('—')),
                DropdownMenuItem(value: 'M', child: Text('男')),
                DropdownMenuItem(value: 'F', child: Text('女')),
              ],
              onChanged: (v) => setState(() => _sex = v),
            ),
            if (_error != null) ...[
              const SizedBox(height: 12),
              Text(_error!,
                  style: TextStyle(color: Theme.of(context).colorScheme.error)),
            ],
          ],
        ),
      ),
      actions: [
        TextButton(
          onPressed: _busy ? null : () => Navigator.of(context).pop(),
          child: const Text('取消'),
        ),
        FilledButton(
          onPressed: _busy ? null : _submit,
          child: _busy
              ? const SizedBox(
                  width: 16,
                  height: 16,
                  child: CircularProgressIndicator(strokeWidth: 2),
                )
              : const Text('创建'),
        ),
      ],
    );
  }
}
