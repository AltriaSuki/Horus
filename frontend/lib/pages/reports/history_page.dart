import 'package:flutter/material.dart';
import 'package:intl/intl.dart';

import '../../models/subject.dart';
import '../../services/app_state.dart';
import '../../services/engine_client.dart';
import '../screening/result_page.dart';

/// Per-subject session history. Lists past sessions and lets the user open
/// the result page for any session that has a saved report.
class ReportsHistoryPage extends StatefulWidget {
  final EngineClient engine;
  final AppState state;
  const ReportsHistoryPage({
    super.key,
    required this.engine,
    required this.state,
  });

  @override
  State<ReportsHistoryPage> createState() => _ReportsHistoryPageState();
}

class _ReportsHistoryPageState extends State<ReportsHistoryPage> {
  Subject? _selected;
  Future<List<_SessionRow>>? _sessionsFuture;

  void _selectSubject(Subject? s) {
    setState(() {
      _selected = s;
      if (s == null) {
        _sessionsFuture = null;
      } else {
        _sessionsFuture = _loadSessions(s.id);
      }
    });
  }

  Future<List<_SessionRow>> _loadSessions(String subjectId) async {
    final raw = await widget.engine.listSubjectSessions(subjectId);
    return raw.map(_SessionRow.fromJson).toList(growable: false);
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.all(24),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text('历史会话与报告',
              style: Theme.of(context).textTheme.headlineSmall),
          const SizedBox(height: 16),
          ListenableBuilder(
            listenable: widget.state,
            builder: (context, _) {
              if (widget.state.loading && widget.state.subjects.isEmpty) {
                return const LinearProgressIndicator();
              }
              final subjects = widget.state.subjects;
              if (subjects.isEmpty) {
                return const Text('请先在"被试"标签创建一个被试');
              }
              if (_selected != null &&
                  !subjects.any((s) => s.id == _selected!.id)) {
                _selected = null;
                _sessionsFuture = null;
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
                          child: Text('${s.displayName} (${s.id})'),
                        ))
                    .toList(),
                onChanged: _selectSubject,
              );
            },
          ),
          const SizedBox(height: 24),
          Expanded(
            child: _sessionsFuture == null
                ? const Center(child: Text('请先选择一个被试'))
                : FutureBuilder<List<_SessionRow>>(
                    future: _sessionsFuture,
                    builder: (context, snap) {
                      if (snap.connectionState != ConnectionState.done) {
                        return const Center(
                            child: CircularProgressIndicator());
                      }
                      if (snap.hasError) {
                        return Text('加载失败: ${snap.error}');
                      }
                      final rows = snap.data ?? const <_SessionRow>[];
                      if (rows.isEmpty) {
                        return Center(
                          child: Column(
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              const Text('该被试还没有历史会话'),
                              const SizedBox(height: 12),
                              FilledButton.tonal(
                                onPressed: () => _selectSubject(_selected),
                                child: const Text('刷新'),
                              ),
                            ],
                          ),
                        );
                      }
                      return RefreshIndicator(
                        onRefresh: () async => _selectSubject(_selected),
                        child: ListView.separated(
                          itemCount: rows.length,
                          separatorBuilder: (_, __) =>
                              const Divider(height: 1),
                          itemBuilder: (context, i) => _SessionTile(
                            row: rows[i],
                            engine: widget.engine,
                            subjectName: _selected?.displayName ?? '',
                          ),
                        ),
                      );
                    },
                  ),
          ),
        ],
      ),
    );
  }
}

class _SessionRow {
  final String id;
  final String kind;
  final String mode;
  final String status;
  final DateTime startedAt;
  final DateTime? endedAt;
  final bool hasReport;

  _SessionRow({
    required this.id,
    required this.kind,
    required this.mode,
    required this.status,
    required this.startedAt,
    required this.endedAt,
    required this.hasReport,
  });

  factory _SessionRow.fromJson(Map<String, dynamic> json) {
    return _SessionRow(
      id: json['id'] as String,
      kind: json['kind'] as String,
      mode: json['mode'] as String,
      status: json['status'] as String,
      startedAt: DateTime.parse(json['started_at'] as String),
      endedAt: json['ended_at'] == null
          ? null
          : DateTime.parse(json['ended_at'] as String),
      hasReport: (json['has_report'] as bool?) ?? false,
    );
  }
}

class _SessionTile extends StatelessWidget {
  final _SessionRow row;
  final EngineClient engine;
  final String subjectName;

  const _SessionTile({
    required this.row,
    required this.engine,
    required this.subjectName,
  });

  @override
  Widget build(BuildContext context) {
    final fmt = DateFormat('yyyy-MM-dd HH:mm');
    final icon = row.kind == 'screening'
        ? Icons.psychology
        : Icons.sports_esports;
    final statusColor = switch (row.status) {
      'completed' => Colors.green,
      'failed' => Colors.red,
      'cancelled' => Colors.orange,
      _ => Colors.grey,
    };
    return ListTile(
      leading: Icon(icon),
      title: Text('${row.kind} · ${row.mode}'),
      subtitle: Text(
        '开始: ${fmt.format(row.startedAt.toLocal())}'
        '${row.endedAt == null ? "" : "  结束: ${fmt.format(row.endedAt!.toLocal())}"}',
      ),
      trailing: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Container(
            padding:
                const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
            decoration: BoxDecoration(
              color: statusColor.withValues(alpha: 0.15),
              borderRadius: BorderRadius.circular(12),
            ),
            child: Text(row.status,
                style: TextStyle(color: statusColor, fontSize: 12)),
          ),
          if (row.hasReport) const Icon(Icons.chevron_right),
        ],
      ),
      onTap: !row.hasReport
          ? null
          : () => Navigator.of(context).push(MaterialPageRoute(
                builder: (_) => ScreeningResultPage(
                  engine: engine,
                  sessionId: row.id,
                  subjectName: subjectName,
                ),
              )),
    );
  }
}
