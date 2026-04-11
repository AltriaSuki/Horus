import 'package:flutter/material.dart';

import '../../models/subject.dart';
import '../../services/app_state.dart';
import '../../theme/app_colors.dart';

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
    return Dialog(
      insetPadding: const EdgeInsets.all(24),
      child: Container(
        padding: const EdgeInsets.all(24),
        constraints: const BoxConstraints(maxWidth: 420),
        child: Form(
          key: _formKey,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              // Header
              Row(
                children: [
                  Container(
                    width: 48,
                    height: 48,
                    decoration: BoxDecoration(
                      color: AppColors.primaryContainer,
                      borderRadius: BorderRadius.circular(16),
                    ),
                    child: const Icon(Icons.person_add_rounded,
                        color: AppColors.primary, size: 26),
                  ),
                  const SizedBox(width: 14),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Text('添加小朋友',
                            style: Theme.of(context).textTheme.titleLarge),
                        Text('填写基本信息开始',
                            style: Theme.of(context).textTheme.bodySmall),
                      ],
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 24),

              TextFormField(
                controller: _idCtrl,
                decoration: const InputDecoration(
                  labelText: '编号',
                  hintText: '例: CHILD_001',
                  prefixIcon: Icon(Icons.badge_outlined),
                ),
                validator: (v) {
                  if (v == null || v.trim().isEmpty) return '请填写编号';
                  if (!RegExp(r'^[A-Za-z0-9_-]+$').hasMatch(v.trim())) {
                    return '只能用字母 / 数字 / _ -';
                  }
                  return null;
                },
              ),
              const SizedBox(height: 14),

              TextFormField(
                controller: _nameCtrl,
                decoration: const InputDecoration(
                  labelText: '昵称',
                  hintText: '叫什么名字都可以',
                  prefixIcon: Icon(Icons.favorite_outline),
                ),
                validator: (v) =>
                    v == null || v.trim().isEmpty ? '请填写昵称' : null,
              ),
              const SizedBox(height: 14),

              // Sex selector as nice toggles instead of dropdown
              Text('性别 (可选)',
                  style: Theme.of(context).textTheme.labelLarge),
              const SizedBox(height: 8),
              Row(
                children: [
                  Expanded(
                    child: _SexTile(
                      emoji: '👦',
                      label: '男孩',
                      selected: _sex == 'M',
                      onTap: () => setState(
                          () => _sex = _sex == 'M' ? null : 'M'),
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: _SexTile(
                      emoji: '👧',
                      label: '女孩',
                      selected: _sex == 'F',
                      onTap: () => setState(
                          () => _sex = _sex == 'F' ? null : 'F'),
                    ),
                  ),
                ],
              ),

              if (_error != null) ...[
                const SizedBox(height: 14),
                Container(
                  padding: const EdgeInsets.all(12),
                  decoration: BoxDecoration(
                    color: AppColors.errorContainer,
                    borderRadius: BorderRadius.circular(12),
                  ),
                  child: Row(
                    children: [
                      const Icon(Icons.error_outline,
                          color: AppColors.error, size: 18),
                      const SizedBox(width: 8),
                      Expanded(
                        child: Text(
                          _error!,
                          style: const TextStyle(
                            color: AppColors.onErrorContainer,
                            fontSize: 13,
                          ),
                        ),
                      ),
                    ],
                  ),
                ),
              ],

              const SizedBox(height: 24),
              Row(
                children: [
                  Expanded(
                    child: OutlinedButton(
                      onPressed: _busy
                          ? null
                          : () => Navigator.of(context).pop(),
                      child: const Text('取消'),
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: FilledButton(
                      onPressed: _busy ? null : _submit,
                      child: _busy
                          ? const SizedBox(
                              width: 18,
                              height: 18,
                              child: CircularProgressIndicator(
                                strokeWidth: 2.5,
                                color: Colors.white,
                              ),
                            )
                          : const Text('创建'),
                    ),
                  ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }
}


class _SexTile extends StatelessWidget {
  final String emoji;
  final String label;
  final bool selected;
  final VoidCallback onTap;

  const _SexTile({
    required this.emoji,
    required this.label,
    required this.selected,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(16),
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 200),
        padding: const EdgeInsets.symmetric(vertical: 16),
        decoration: BoxDecoration(
          color: selected
              ? AppColors.primaryContainer
              : AppColors.surfaceContainer,
          borderRadius: BorderRadius.circular(16),
          border: Border.all(
            color: selected ? AppColors.primary : AppColors.divider,
            width: 2,
          ),
        ),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(emoji, style: const TextStyle(fontSize: 32)),
            const SizedBox(height: 4),
            Text(label,
                style: TextStyle(
                  fontSize: 14,
                  fontWeight: FontWeight.w600,
                  color: selected
                      ? AppColors.onPrimaryContainer
                      : AppColors.onSurface,
                )),
          ],
        ),
      ),
    );
  }
}
