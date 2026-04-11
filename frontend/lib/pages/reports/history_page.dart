import 'package:flutter/material.dart';
import 'package:intl/intl.dart';

import '../../models/subject.dart';
import '../../services/app_state.dart';
import '../../services/engine_client.dart';
import '../../theme/app_colors.dart';
import '../screening/result_page.dart';

/// Per-subject session history.
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
      _sessionsFuture = s == null ? null : _loadSessions(s.id);
    });
  }

  Future<List<_SessionRow>> _loadSessions(String subjectId) async {
    final raw = await widget.engine.listSubjectSessions(subjectId);
    return raw.map(_SessionRow.fromJson).toList(growable: false);
  }

  @override
  Widget build(BuildContext context) {
    return SingleChildScrollView(
      padding: const EdgeInsets.fromLTRB(20, 8, 20, 32),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text('历史记录',
              style: Theme.of(context).textTheme.headlineSmall),
          const SizedBox(height: 4),
          Text('查看每位小朋友的历次闯关记录',
              style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                    color: AppColors.onSurfaceMuted,
                  )),
          const SizedBox(height: 20),

          ListenableBuilder(
            listenable: widget.state,
            builder: (context, _) {
              if (widget.state.loading && widget.state.subjects.isEmpty) {
                return const LinearProgressIndicator();
              }
              final subjects = widget.state.subjects;
              if (subjects.isEmpty) {
                return _emptyCard('还没有小朋友\n请先到"被试"标签添加一位');
              }
              if (_selected != null &&
                  !subjects.any((s) => s.id == _selected!.id)) {
                _selected = null;
                _sessionsFuture = null;
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
                      child: Text('选择一位小朋友'),
                    ),
                    items: subjects
                        .map((s) => DropdownMenuItem(
                              value: s,
                              child: Padding(
                                padding: const EdgeInsets.symmetric(
                                    vertical: 8),
                                child: Text(
                                    '${s.displayName} (${s.id})'),
                              ),
                            ))
                        .toList(),
                    onChanged: _selectSubject,
                  ),
                ),
              );
            },
          ),
          const SizedBox(height: 24),

          if (_sessionsFuture == null)
            _emptyCard('请先选择一位小朋友')
          else
            FutureBuilder<List<_SessionRow>>(
              future: _sessionsFuture,
              builder: (context, snap) {
                if (snap.connectionState != ConnectionState.done) {
                  return const Padding(
                    padding: EdgeInsets.all(24),
                    child: Center(child: CircularProgressIndicator()),
                  );
                }
                if (snap.hasError) {
                  return _emptyCard('加载失败: ${snap.error}');
                }
                final rows = snap.data ?? const <_SessionRow>[];
                if (rows.isEmpty) {
                  return _emptyCard('该小朋友还没有历史记录');
                }
                return Column(
                  children: rows.map((r) => Padding(
                        padding: const EdgeInsets.only(bottom: 12),
                        child: _SessionTile(
                          row: r,
                          engine: widget.engine,
                          subjectName: _selected?.displayName ?? '',
                        ),
                      )).toList(),
                );
              },
            ),
        ],
      ),
    );
  }

  Widget _emptyCard(String msg) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(32),
      decoration: BoxDecoration(
        color: AppColors.surfaceContainer,
        borderRadius: BorderRadius.circular(22),
        border: Border.all(color: AppColors.divider),
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          const Icon(Icons.inbox_rounded,
              size: 48, color: AppColors.onSurfaceFaint),
          const SizedBox(height: 12),
          Text(
            msg,
            textAlign: TextAlign.center,
            style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                  color: AppColors.onSurfaceMuted,
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
    final fmt = DateFormat('MM-dd HH:mm');
    final (icon, accent, label) = row.kind == 'screening'
        ? (Icons.rocket_launch_rounded, AppColors.primary, '早筛')
        : (Icons.sports_esports_rounded, AppColors.secondary, '训练');

    final (statusColor, statusLabel) = switch (row.status) {
      'completed' => (AppColors.secondary, '已完成'),
      'failed' => (AppColors.error, '失败'),
      'cancelled' => (AppColors.tertiary, '取消'),
      'running' => (AppColors.primary, '运行中'),
      _ => (AppColors.onSurfaceMuted, row.status),
    };

    return InkWell(
      onTap: !row.hasReport
          ? null
          : () => Navigator.of(context).push(MaterialPageRoute(
                builder: (_) => ScreeningResultPage(
                  engine: engine,
                  sessionId: row.id,
                  subjectName: subjectName,
                ),
              )),
      borderRadius: BorderRadius.circular(20),
      child: Container(
        padding: const EdgeInsets.all(16),
        decoration: BoxDecoration(
          color: AppColors.surface,
          borderRadius: BorderRadius.circular(20),
          border: Border.all(color: AppColors.divider),
        ),
        child: Row(
          children: [
            Container(
              width: 52,
              height: 52,
              decoration: BoxDecoration(
                color: accent.withValues(alpha: 0.15),
                borderRadius: BorderRadius.circular(16),
              ),
              child: Icon(icon, color: accent, size: 26),
            ),
            const SizedBox(width: 14),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisSize: MainAxisSize.min,
                children: [
                  Row(
                    children: [
                      Text(label,
                          style:
                              Theme.of(context).textTheme.titleMedium),
                      const SizedBox(width: 8),
                      Container(
                        padding: const EdgeInsets.symmetric(
                            horizontal: 8, vertical: 2),
                        decoration: BoxDecoration(
                          color: statusColor.withValues(alpha: 0.14),
                          borderRadius: BorderRadius.circular(10),
                        ),
                        child: Text(
                          statusLabel,
                          style: TextStyle(
                            fontSize: 11,
                            fontWeight: FontWeight.w700,
                            color: statusColor,
                          ),
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 4),
                  Text(
                    '开始: ${fmt.format(row.startedAt.toLocal())}'
                    '${row.endedAt == null ? "" : "   结束: ${fmt.format(row.endedAt!.toLocal())}"}',
                    style: Theme.of(context).textTheme.bodySmall,
                  ),
                ],
              ),
            ),
            if (row.hasReport)
              const Icon(Icons.chevron_right_rounded,
                  color: AppColors.primary)
            else
              const SizedBox(width: 24),
          ],
        ),
      ),
    );
  }
}
