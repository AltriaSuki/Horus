import 'package:fl_chart/fl_chart.dart';
import 'package:flutter/material.dart';

import '../../models/report.dart';
import '../../services/engine_client.dart';

/// Renders the AdhdReport once the worker has finished. Polls
/// `GET /sessions/{id}/report` until it returns 200, then displays:
///
/// * Headline prediction + risk level + probability gauge
/// * Bar chart of the 12 selected feature values
/// * Bar chart of the same features' importance weights
/// * Raw JSON inspector at the bottom for diagnostics
class ScreeningResultPage extends StatefulWidget {
  final EngineClient engine;
  final String sessionId;
  final String subjectName;

  const ScreeningResultPage({
    super.key,
    required this.engine,
    required this.sessionId,
    required this.subjectName,
  });

  @override
  State<ScreeningResultPage> createState() => _ScreeningResultPageState();
}

class _ScreeningResultPageState extends State<ScreeningResultPage> {
  AdhdReport? _report;
  String? _error;

  @override
  void initState() {
    super.initState();
    _loadWithRetry();
  }

  Future<void> _loadWithRetry() async {
    for (var i = 0; i < 20; i++) {
      try {
        final r = await widget.engine.getReport(widget.sessionId);
        if (r != null) {
          if (!mounted) return;
          setState(() => _report = r);
          return;
        }
      } catch (e) {
        if (!mounted) return;
        setState(() => _error = e.toString());
        return;
      }
      await Future<void>.delayed(const Duration(seconds: 1));
    }
    if (mounted) {
      setState(() => _error = '报告加载超时');
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text('报告 — ${widget.subjectName}'),
      ),
      body: _report == null
          ? Center(
              child: _error != null
                  ? Text(_error!)
                  : const CircularProgressIndicator(),
            )
          : _ReportBody(report: _report!),
    );
  }
}

class _ReportBody extends StatelessWidget {
  final AdhdReport report;
  const _ReportBody({required this.report});

  Color _riskColor(BuildContext context) {
    switch (report.riskLevel) {
      case 'HIGH':
        return Colors.red.shade700;
      case 'MODERATE':
        return Colors.orange.shade700;
      case 'LOW':
        return Colors.amber.shade700;
      default:
        return Colors.green.shade700;
    }
  }

  String _riskLabel() {
    switch (report.riskLevel) {
      case 'HIGH':
        return '高风险';
      case 'MODERATE':
        return '中等风险';
      case 'LOW':
        return '较低风险';
      case 'MINIMAL':
        return '极低风险';
      default:
        return report.riskLevel;
    }
  }

  @override
  Widget build(BuildContext context) {
    return SingleChildScrollView(
      padding: const EdgeInsets.all(24),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          // ===== Headline ====================================================
          Card(
            color: _riskColor(context).withValues(alpha: 0.08),
            child: Padding(
              padding: const EdgeInsets.all(20),
              child: Row(
                children: [
                  Container(
                    width: 80,
                    height: 80,
                    decoration: BoxDecoration(
                      color: _riskColor(context),
                      shape: BoxShape.circle,
                    ),
                    child: Center(
                      child: Text(
                        '${(report.adhdProbability * 100).toStringAsFixed(0)}%',
                        style: const TextStyle(
                          color: Colors.white,
                          fontSize: 22,
                          fontWeight: FontWeight.bold,
                        ),
                      ),
                    ),
                  ),
                  const SizedBox(width: 24),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          report.prediction == 'ADHD' ? 'ADHD 倾向' : '正常',
                          style: Theme.of(context).textTheme.headlineMedium,
                        ),
                        const SizedBox(height: 4),
                        Text('风险等级: ${_riskLabel()}',
                            style: TextStyle(color: _riskColor(context))),
                        const SizedBox(height: 4),
                        Text(report.modelInfo,
                            style: Theme.of(context).textTheme.bodySmall),
                      ],
                    ),
                  ),
                ],
              ),
            ),
          ),

          if (report.qualityWarnings.isNotEmpty) ...[
            const SizedBox(height: 16),
            Card(
              color: Theme.of(context).colorScheme.errorContainer,
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Row(
                  children: [
                    const Icon(Icons.warning_amber),
                    const SizedBox(width: 8),
                    Expanded(child: Text('数据质量警告: ${report.qualityWarnings}')),
                  ],
                ),
              ),
            ),
          ],

          // ===== Feature importance bar chart ================================
          const SizedBox(height: 24),
          Text('解释性特征 (Top-12 RF Importance)',
              style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 12),
          SizedBox(
            height: 280,
            child: _FeatureImportanceChart(report: report),
          ),

          // ===== Selected feature values =====================================
          const SizedBox(height: 24),
          Text('选定特征值',
              style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 12),
          ..._sortedValues().map((entry) => ListTile(
                dense: true,
                title: Text(entry.key),
                trailing: Text(entry.value.toStringAsFixed(4)),
              )),

          // ===== Raw report dump =============================================
          const SizedBox(height: 24),
          ExpansionTile(
            title: const Text('原始数据 (诊断用)'),
            children: [
              Padding(
                padding: const EdgeInsets.all(16),
                child: SelectableText(
                  'session_id: ${report.sessionId}\n'
                  'created_at: ${report.createdAt.toIso8601String()}\n'
                  'adhd_probability: ${report.adhdProbability}\n'
                  'control_probability: ${report.controlProbability}',
                  style: const TextStyle(fontFamily: 'monospace', fontSize: 12),
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }

  List<MapEntry<String, double>> _sortedValues() {
    final entries = report.featureValues.entries.toList()
      ..sort((a, b) {
        final ia = report.featureImportance[a.key] ?? 0;
        final ib = report.featureImportance[b.key] ?? 0;
        return ib.compareTo(ia);
      });
    return entries;
  }
}

class _FeatureImportanceChart extends StatelessWidget {
  final AdhdReport report;
  const _FeatureImportanceChart({required this.report});

  @override
  Widget build(BuildContext context) {
    final entries = report.featureImportance.entries.toList()
      ..sort((a, b) => b.value.compareTo(a.value));

    if (entries.isEmpty) {
      return const Center(child: Text('无重要性数据'));
    }

    final maxImp = entries.first.value;

    return BarChart(
      BarChartData(
        alignment: BarChartAlignment.spaceAround,
        maxY: maxImp * 1.15,
        barGroups: List.generate(entries.length, (i) {
          return BarChartGroupData(
            x: i,
            barRods: [
              BarChartRodData(
                toY: entries[i].value,
                color: Theme.of(context).colorScheme.primary,
                width: 16,
                borderRadius: const BorderRadius.vertical(top: Radius.circular(4)),
              ),
            ],
          );
        }),
        titlesData: FlTitlesData(
          show: true,
          topTitles: const AxisTitles(
              sideTitles: SideTitles(showTitles: false)),
          rightTitles: const AxisTitles(
              sideTitles: SideTitles(showTitles: false)),
          leftTitles: const AxisTitles(
            sideTitles: SideTitles(showTitles: true, reservedSize: 40),
          ),
          bottomTitles: AxisTitles(
            sideTitles: SideTitles(
              showTitles: true,
              reservedSize: 80,
              getTitlesWidget: (value, meta) {
                final i = value.toInt();
                if (i < 0 || i >= entries.length) return const SizedBox();
                return Padding(
                  padding: const EdgeInsets.only(top: 8),
                  child: Transform.rotate(
                    angle: -0.6,
                    child: Text(
                      entries[i].key,
                      style: const TextStyle(fontSize: 10),
                    ),
                  ),
                );
              },
            ),
          ),
        ),
        gridData: const FlGridData(show: true),
        borderData: FlBorderData(show: false),
      ),
    );
  }
}
