import 'package:flutter/material.dart';

import '../../models/subject.dart';
import '../../services/app_state.dart';
import '../../services/engine_client.dart';
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
  Future<void> _openCreateDialog() async {
    final created = await showDialog<Subject>(
      context: context,
      builder: (_) => SubjectCreateDialog(state: widget.state),
    );
    // The dialog already calls AppState.createSubject which refreshes the
    // shared cache, so listening pages update automatically. We don't need
    // to do anything else here.
    if (created != null && mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('已添加 ${created.displayName}')),
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
            return Center(
              child: Padding(
                padding: const EdgeInsets.all(24),
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    const Icon(Icons.error_outline, size: 48),
                    const SizedBox(height: 12),
                    Text('无法加载被试列表\n${widget.state.error}',
                        textAlign: TextAlign.center),
                    const SizedBox(height: 12),
                    FilledButton.tonal(
                      onPressed: () => widget.state.refreshSubjects(),
                      child: const Text('重试'),
                    ),
                  ],
                ),
              ),
            );
          }
          final subjects = widget.state.subjects;
          if (subjects.isEmpty) {
            return Center(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  const Icon(Icons.person_add_alt_1,
                      size: 64, color: Colors.grey),
                  const SizedBox(height: 12),
                  const Text('还没有被试'),
                  const SizedBox(height: 8),
                  FilledButton(
                    onPressed: _openCreateDialog,
                    child: const Text('添加第一个被试'),
                  ),
                ],
              ),
            );
          }
          return RefreshIndicator(
            onRefresh: () => widget.state.refreshSubjects(),
            child: ListView.separated(
              padding: const EdgeInsets.all(16),
              itemCount: subjects.length,
              separatorBuilder: (_, __) => const Divider(height: 1),
              itemBuilder: (context, i) {
                final s = subjects[i];
                return ListTile(
                  leading: CircleAvatar(child: Text(s.id.substring(0, 1))),
                  title: Text(s.displayName),
                  subtitle: Text(
                      'ID: ${s.id}${s.sex != null ? " · ${s.sex}" : ""}'),
                  trailing: const Icon(Icons.chevron_right),
                  onTap: () {
                    // Detail view is Phase 3 — for now, the screening tab is
                    // where you select a subject to run.
                  },
                );
              },
            ),
          );
        },
      ),
      floatingActionButton: FloatingActionButton.extended(
        onPressed: _openCreateDialog,
        icon: const Icon(Icons.add),
        label: const Text('添加被试'),
      ),
    );
  }
}
