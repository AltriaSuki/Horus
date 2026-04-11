import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../../models/report.dart';
import '../../services/engine_client.dart';
import '../../theme/app_colors.dart';

/// Renders the AdhdReport once the worker has finished.
///
/// Design:
///   * Big risk probability ring at the top — the one thing parents look at
///   * Simple "for the kid" sticker below ("太棒啦！挑战完成！")
///   * Expandable "for the parent" detailed panel with per-feature info
///   * All risk levels mapped to warm colors from the palette
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
        title: Text('${widget.subjectName} 的报告'),
      ),
      body: _report == null
          ? Center(
              child: _error != null
                  ? Padding(
                      padding: const EdgeInsets.all(24),
                      child: Text(_error!, textAlign: TextAlign.center),
                    )
                  : Column(
                      mainAxisAlignment: MainAxisAlignment.center,
                      children: [
                        const CircularProgressIndicator(),
                        const SizedBox(height: 16),
                        Text('报告生成中…',
                            style: Theme.of(context)
                                .textTheme
                                .titleMedium
                                ?.copyWith(
                                  color: AppColors.onSurfaceMuted,
                                )),
                      ],
                    ),
            )
          : _ReportBody(report: _report!, subjectName: widget.subjectName),
    );
  }
}


// ───────────────────────────────────────────────────────────────────────────
// Report body — hero + kid sticker + parent details
// ───────────────────────────────────────────────────────────────────────────


class _ReportBody extends StatelessWidget {
  final AdhdReport report;
  final String subjectName;
  const _ReportBody({required this.report, required this.subjectName});

  Color get _riskColor {
    switch (report.riskLevel) {
      case 'HIGH':
        return AppColors.riskHigh;
      case 'MODERATE':
        return AppColors.riskModerate;
      case 'LOW':
        return AppColors.riskLow;
      default:
        return AppColors.riskMinimal;
    }
  }

  String get _riskLabel {
    switch (report.riskLevel) {
      case 'HIGH':
        return '高关注';
      case 'MODERATE':
        return '中等关注';
      case 'LOW':
        return '较低关注';
      case 'MINIMAL':
        return '极低关注';
      default:
        return report.riskLevel;
    }
  }

  String get _kidMessage {
    switch (report.riskLevel) {
      case 'HIGH':
      case 'MODERATE':
        return '你真棒！你已经完成了挑战 🌟';
      default:
        return '太厉害啦！全部完成 🎉';
    }
  }

  String get _parentSummary {
    switch (report.riskLevel) {
      case 'HIGH':
        return '本次筛查结果提示注意力模式与注意缺陷多动特征有较强关联。'
            '建议结合医生评估进一步确认。';
      case 'MODERATE':
        return '本次筛查结果提示部分注意力指标存在差异。'
            '建议观察日常表现，必要时咨询专业医师。';
      case 'LOW':
        return '本次筛查结果显示注意力指标整体接近典型儿童范围，'
            '少数特征略有差异。';
      case 'MINIMAL':
        return '本次筛查结果显示注意力指标与典型儿童非常接近。';
      default:
        return '';
    }
  }

  @override
  Widget build(BuildContext context) {
    return SingleChildScrollView(
      padding: const EdgeInsets.fromLTRB(20, 8, 20, 32),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          // ===== Hero: big risk ring ========================================
          Container(
            padding: const EdgeInsets.all(24),
            decoration: BoxDecoration(
              color: AppColors.surface,
              borderRadius: BorderRadius.circular(28),
              border: Border.all(color: AppColors.divider),
            ),
            child: Column(
              children: [
                SizedBox(
                  width: 220,
                  height: 220,
                  child: Stack(
                    alignment: Alignment.center,
                    children: [
                      CustomPaint(
                        size: const Size(220, 220),
                        painter: _RiskRingPainter(
                          progress: 1,
                          color: AppColors.surfaceContainerHigh,
                          stroke: 20,
                        ),
                      ),
                      CustomPaint(
                        size: const Size(220, 220),
                        painter: _RiskRingPainter(
                          progress: report.adhdProbability,
                          color: _riskColor,
                          stroke: 20,
                          rounded: true,
                        ),
                      ),
                      Column(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          Text(
                            '${(report.adhdProbability * 100).round()}%',
                            style: TextStyle(
                              fontSize: 56,
                              fontWeight: FontWeight.w800,
                              color: _riskColor,
                            ),
                          ),
                          Text('ADHD 概率',
                              style: Theme.of(context)
                                  .textTheme
                                  .labelMedium),
                        ],
                      ),
                    ],
                  ),
                ),
                const SizedBox(height: 16),
                Container(
                  padding: const EdgeInsets.symmetric(
                      horizontal: 16, vertical: 8),
                  decoration: BoxDecoration(
                    color: _riskColor.withValues(alpha: 0.14),
                    borderRadius: BorderRadius.circular(20),
                  ),
                  child: Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Icon(Icons.flag_rounded, color: _riskColor, size: 18),
                      const SizedBox(width: 6),
                      Text(
                        '风险等级：$_riskLabel',
                        style: TextStyle(
                          color: _riskColor,
                          fontWeight: FontWeight.w700,
                          fontSize: 14,
                        ),
                      ),
                    ],
                  ),
                ),
              ],
            ),
          ),

          const SizedBox(height: 20),

          // ===== Kid-facing message ========================================
          Container(
            padding: const EdgeInsets.all(20),
            decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(24),
              gradient: const LinearGradient(
                begin: Alignment.topLeft,
                end: Alignment.bottomRight,
                colors: [Color(0xFFFFF0C2), Color(0xFFFFE4CC)],
              ),
            ),
            child: Row(
              children: [
                const Text('🏆', style: TextStyle(fontSize: 44)),
                const SizedBox(width: 16),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Text('$subjectName 的成绩',
                          style:
                              Theme.of(context).textTheme.titleMedium),
                      const SizedBox(height: 2),
                      Text(_kidMessage,
                          style:
                              Theme.of(context).textTheme.bodyLarge),
                    ],
                  ),
                ),
              ],
            ),
          ),

          const SizedBox(height: 20),

          // ===== Parent summary =============================================
          Container(
            padding: const EdgeInsets.all(20),
            decoration: BoxDecoration(
              color: AppColors.surface,
              borderRadius: BorderRadius.circular(24),
              border: Border.all(color: AppColors.divider),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Row(
                  children: [
                    Icon(Icons.family_restroom_rounded,
                        color: AppColors.secondary),
                    SizedBox(width: 8),
                    Text('给家长的说明',
                        style: TextStyle(
                          fontSize: 16,
                          fontWeight: FontWeight.w700,
                          color: AppColors.onSurface,
                        )),
                  ],
                ),
                const SizedBox(height: 12),
                Text(
                  _parentSummary,
                  style:
                      Theme.of(context).textTheme.bodyLarge?.copyWith(
                            height: 1.6,
                          ),
                ),
                const SizedBox(height: 12),
                Container(
                  padding: const EdgeInsets.all(12),
                  decoration: BoxDecoration(
                    color: AppColors.surfaceContainer,
                    borderRadius: BorderRadius.circular(14),
                  ),
                  child: Row(
                    children: [
                      const Icon(Icons.info_outline,
                          size: 18, color: AppColors.onSurfaceMuted),
                      const SizedBox(width: 8),
                      Expanded(
                        child: Text(
                          '本工具为科研辅助筛查，不能替代专业医师诊断。',
                          style: Theme.of(context)
                              .textTheme
                              .bodySmall
                              ?.copyWith(fontSize: 12),
                        ),
                      ),
                    ],
                  ),
                ),
              ],
            ),
          ),

          const SizedBox(height: 20),

          // ===== Feature importance =========================================
          Text('关键指标',
              style: Theme.of(context).textTheme.titleLarge),
          const SizedBox(height: 4),
          Text('按对本次判断的影响力从高到低排序',
              style: Theme.of(context).textTheme.bodySmall),
          const SizedBox(height: 12),
          ..._sortedFeatures().map((entry) => _FeatureBar(
                name: entry.key,
                value: report.featureValues[entry.key] ?? 0,
                importance: entry.value,
                maxImportance: _maxImportance(),
              )),

          const SizedBox(height: 20),

          // ===== Diagnostic dump ============================================
          ExpansionTile(
            leading: const Icon(Icons.bug_report_outlined),
            title: const Text('诊断数据 (高级)'),
            shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(20),
              side: const BorderSide(color: AppColors.divider),
            ),
            collapsedShape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(20),
              side: const BorderSide(color: AppColors.divider),
            ),
            backgroundColor: AppColors.surfaceContainer,
            collapsedBackgroundColor: AppColors.surfaceContainer,
            children: [
              Padding(
                padding: const EdgeInsets.fromLTRB(20, 0, 20, 16),
                child: SelectableText(
                  'session_id: ${report.sessionId}\n'
                  'created_at: ${report.createdAt.toIso8601String()}\n'
                  'adhd_probability: ${report.adhdProbability}\n'
                  'control_probability: ${report.controlProbability}\n'
                  'model: ${report.modelInfo}',
                  style: const TextStyle(
                      fontFamily: 'monospace', fontSize: 12),
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }

  List<MapEntry<String, double>> _sortedFeatures() {
    final entries = report.featureImportance.entries.toList()
      ..sort((a, b) => b.value.compareTo(a.value));
    return entries;
  }

  double _maxImportance() {
    if (report.featureImportance.isEmpty) return 1.0;
    return report.featureImportance.values
        .reduce((a, b) => a > b ? a : b);
  }
}


// ───────────────────────────────────────────────────────────────────────────
// Risk ring painter
// ───────────────────────────────────────────────────────────────────────────


class _RiskRingPainter extends CustomPainter {
  final double progress;
  final Color color;
  final double stroke;
  final bool rounded;

  _RiskRingPainter({
    required this.progress,
    required this.color,
    required this.stroke,
    this.rounded = false,
  });

  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()
      ..color = color
      ..style = PaintingStyle.stroke
      ..strokeWidth = stroke
      ..strokeCap = rounded ? StrokeCap.round : StrokeCap.butt;
    final rect = Rect.fromLTWH(
      stroke / 2,
      stroke / 2,
      size.width - stroke,
      size.height - stroke,
    );
    canvas.drawArc(
      rect,
      -math.pi / 2,
      progress.clamp(0.0, 1.0) * 2 * math.pi,
      false,
      paint,
    );
  }

  @override
  bool shouldRepaint(covariant _RiskRingPainter old) =>
      old.progress != progress || old.color != color;
}


// ───────────────────────────────────────────────────────────────────────────
// Feature importance row
// ───────────────────────────────────────────────────────────────────────────


class _FeatureBar extends StatelessWidget {
  final String name;
  final double value;
  final double importance;
  final double maxImportance;

  const _FeatureBar({
    required this.name,
    required this.value,
    required this.importance,
    required this.maxImportance,
  });

  static const Map<String, String> _friendlyNames = {
    'cv_rt': '反应时稳定性',
    'mean_rt': '平均反应时间',
    'std_rt': '反应时波动',
    'accuracy': '正确率',
    'rt_diff_correct_incorrect': '对错反应差',
    'pupil_mean_change': '瞳孔平均变化',
    'pupil_std_of_means': '瞳孔响应稳定性',
    'pupil_slope': '瞳孔上升速率',
    'pupil_auc': '瞳孔总投入',
    'gaze_var_x': '注视横向分散',
    'mean_rt_load1': '低负荷反应时',
    'mean_rt_load2': '高负荷反应时',
  };

  @override
  Widget build(BuildContext context) {
    final friendly = _friendlyNames[name] ?? name;
    final normalised =
        maxImportance <= 0 ? 0.0 : (importance / maxImportance).clamp(0.0, 1.0);

    return Container(
      margin: const EdgeInsets.only(bottom: 10),
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: BorderRadius.circular(18),
        border: Border.all(color: AppColors.divider),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Text(friendly,
                        style: Theme.of(context).textTheme.titleMedium),
                    Text(name,
                        style: Theme.of(context).textTheme.bodySmall),
                  ],
                ),
              ),
              Text(
                value.toStringAsFixed(3),
                style: const TextStyle(
                  fontFamily: 'monospace',
                  fontSize: 14,
                  fontWeight: FontWeight.w700,
                  color: AppColors.primary,
                ),
              ),
            ],
          ),
          const SizedBox(height: 8),
          ClipRRect(
            borderRadius: BorderRadius.circular(10),
            child: LinearProgressIndicator(
              value: normalised,
              minHeight: 8,
              backgroundColor: AppColors.surfaceContainerHigh,
              valueColor: const AlwaysStoppedAnimation(AppColors.primary),
            ),
          ),
        ],
      ),
    );
  }
}
