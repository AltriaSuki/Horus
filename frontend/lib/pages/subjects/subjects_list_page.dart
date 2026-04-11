import 'package:flutter/material.dart';

import '../../models/subject.dart';
import '../../services/app_state.dart';
import '../../services/engine_client.dart';
import '../../theme/app_colors.dart';
import 'subject_create_dialog.dart';

class SubjectsListPage extends StatefulWidget {
  final EngineClient engine;
  final AppState state;
  const SubjectsListPage({
    super.key,
    required this.engine,
    required this.state,
  });

  @override
  State<SubjectsListPage> createState() => _SubjectsListPageState();
}

class _SubjectsListPageState extends State<SubjectsListPage> {
  // Palette used to color avatars — pulls from our warm theme so every
  // child gets a distinct but still on-brand tile.
  static const List<Color> _avatarColors = [
    AppColors.primary,
    AppColors.secondary,
    AppColors.tertiary,
    Color(0xFFE76F51),  // 柿子红
    Color(0xFF9B5DE5),  // 柔紫
    Color(0xFF06A77D),  // 深绿
  ];

  Future<void> _openCreateDialog() async {
    final created = await showDialog<Subject>(
      context: context,
      builder: (_) => SubjectCreateDialog(state: widget.state),
    );
    if (created != null && mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Row(
            children: [
              const Icon(Icons.check_circle_rounded, color: AppColors.secondary),
              const SizedBox(width: 12),
              Text('已添加 ${created.displayName}'),
            ],
          ),
          duration: const Duration(seconds: 2),
        ),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: ListenableBuilder(
        listenable: widget.state,
        builder: (context, _) {
          if (widget.state.loading && widget.state.subjects.isEmpty) {
            return const Center(child: CircularProgressIndicator());
          }
          if (widget.state.error != null && widget.state.subjects.isEmpty) {
            return _ErrorBody(
              message: widget.state.error!,
              onRetry: () => widget.state.refreshSubjects(),
            );
          }
          final subjects = widget.state.subjects;
          if (subjects.isEmpty) {
            return _EmptyBody(onAdd: _openCreateDialog);
          }
          return RefreshIndicator(
            color: AppColors.primary,
            onRefresh: () => widget.state.refreshSubjects(),
            child: ListView.builder(
              padding: const EdgeInsets.fromLTRB(20, 8, 20, 100),
              itemCount: subjects.length + 1,
              itemBuilder: (context, i) {
                if (i == 0) {
                  return Padding(
                    padding: const EdgeInsets.only(bottom: 16, top: 4),
                    child: Text(
                      '${subjects.length} 位小朋友',
                      style: Theme.of(context).textTheme.titleLarge,
                    ),
                  );
                }
                final s = subjects[i - 1];
                final color = _avatarColors[(i - 1) % _avatarColors.length];
                return Padding(
                  padding: const EdgeInsets.only(bottom: 12),
                  child: _SubjectTile(subject: s, color: color),
                );
              },
            ),
          );
        },
      ),
      floatingActionButton: FloatingActionButton.extended(
        onPressed: _openCreateDialog,
        icon: const Icon(Icons.add_rounded),
        label: const Text('添加小朋友'),
      ),
    );
  }
}


// ───────────────────────────────────────────────────────────────────────────
// Subject tile
// ───────────────────────────────────────────────────────────────────────────


class _SubjectTile extends StatelessWidget {
  final Subject subject;
  final Color color;

  const _SubjectTile({required this.subject, required this.color});

  @override
  Widget build(BuildContext context) {
    final letter = subject.displayName.isNotEmpty
        ? subject.displayName[0]
        : subject.id[0];
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: AppColors.divider),
      ),
      child: Row(
        children: [
          Container(
            width: 56,
            height: 56,
            decoration: BoxDecoration(
              color: color.withValues(alpha: 0.18),
              borderRadius: BorderRadius.circular(18),
            ),
            alignment: Alignment.center,
            child: Text(
              letter,
              style: TextStyle(
                color: color,
                fontSize: 26,
                fontWeight: FontWeight.w800,
              ),
            ),
          ),
          const SizedBox(width: 16),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: [
                Text(subject.displayName,
                    style: Theme.of(context).textTheme.titleMedium),
                const SizedBox(height: 2),
                Row(
                  children: [
                    Container(
                      padding: const EdgeInsets.symmetric(
                          horizontal: 8, vertical: 2),
                      decoration: BoxDecoration(
                        color: AppColors.surfaceContainer,
                        borderRadius: BorderRadius.circular(10),
                      ),
                      child: Text(
                        'ID ${subject.id}',
                        style: Theme.of(context).textTheme.labelSmall,
                      ),
                    ),
                    if (subject.sex != null) ...[
                      const SizedBox(width: 6),
                      Text(
                        subject.sex == 'M' ? '👦' : '👧',
                        style: const TextStyle(fontSize: 14),
                      ),
                    ],
                  ],
                ),
              ],
            ),
          ),
          const Icon(Icons.chevron_right_rounded,
              color: AppColors.onSurfaceFaint),
        ],
      ),
    );
  }
}


// ───────────────────────────────────────────────────────────────────────────
// Empty state
// ───────────────────────────────────────────────────────────────────────────


class _EmptyBody extends StatelessWidget {
  final VoidCallback onAdd;
  const _EmptyBody({required this.onAdd});

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Container(
              width: 120,
              height: 120,
              decoration: BoxDecoration(
                color: AppColors.primaryContainer,
                borderRadius: BorderRadius.circular(40),
              ),
              child: const Icon(Icons.child_care_rounded,
                  size: 72, color: AppColors.primary),
            ),
            const SizedBox(height: 24),
            Text('还没有小朋友',
                style: Theme.of(context).textTheme.headlineSmall),
            const SizedBox(height: 8),
            Text(
              '添加一位小朋友，开始第一次闯关挑战吧',
              textAlign: TextAlign.center,
              style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                    color: AppColors.onSurfaceMuted,
                  ),
            ),
            const SizedBox(height: 24),
            FilledButton.icon(
              onPressed: onAdd,
              icon: const Icon(Icons.add_rounded),
              label: const Text('添加第一位小朋友'),
            ),
          ],
        ),
      ),
    );
  }
}


// ───────────────────────────────────────────────────────────────────────────
// Error state
// ───────────────────────────────────────────────────────────────────────────


class _ErrorBody extends StatelessWidget {
  final String message;
  final VoidCallback onRetry;

  const _ErrorBody({required this.message, required this.onRetry});

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Container(
              width: 88,
              height: 88,
              decoration: const BoxDecoration(
                color: AppColors.errorContainer,
                shape: BoxShape.circle,
              ),
              child: const Icon(Icons.cloud_off_rounded,
                  size: 44, color: AppColors.error),
            ),
            const SizedBox(height: 20),
            Text('加载失败',
                style: Theme.of(context).textTheme.headlineSmall),
            const SizedBox(height: 8),
            Text(
              message,
              textAlign: TextAlign.center,
              style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                    color: AppColors.onSurfaceMuted,
                  ),
            ),
            const SizedBox(height: 20),
            OutlinedButton.icon(
              onPressed: onRetry,
              icon: const Icon(Icons.refresh_rounded),
              label: const Text('重试'),
            ),
          ],
        ),
      ),
    );
  }
}
