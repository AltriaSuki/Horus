/// Mirrors the response of `GET /sessions/{id}/report`.
class AdhdReport {
  final String sessionId;
  final String prediction; // 'ADHD' | 'Control'
  final double adhdProbability;
  final double controlProbability;
  final String riskLevel; // HIGH | MODERATE | LOW | MINIMAL
  final Map<String, double> featureValues;
  final Map<String, double> featureImportance;
  final String modelInfo;
  final String qualityWarnings;
  final DateTime createdAt;

  const AdhdReport({
    required this.sessionId,
    required this.prediction,
    required this.adhdProbability,
    required this.controlProbability,
    required this.riskLevel,
    required this.featureValues,
    required this.featureImportance,
    required this.modelInfo,
    required this.qualityWarnings,
    required this.createdAt,
  });

  factory AdhdReport.fromJson(Map<String, dynamic> json) {
    Map<String, double> toDoubleMap(dynamic raw) {
      if (raw == null) return const {};
      final m = raw as Map<String, dynamic>;
      return m.map((k, v) => MapEntry(k, (v as num).toDouble()));
    }

    return AdhdReport(
      sessionId: json['session_id'] as String,
      prediction: json['prediction'] as String,
      adhdProbability: (json['adhd_probability'] as num).toDouble(),
      controlProbability: (json['control_probability'] as num).toDouble(),
      riskLevel: json['risk_level'] as String,
      featureValues: toDoubleMap(json['feature_values']),
      featureImportance: toDoubleMap(json['feature_importance']),
      modelInfo: (json['model_info'] as String?) ?? '',
      qualityWarnings: (json['quality_warnings'] as String?) ?? '',
      createdAt: DateTime.parse(json['created_at'] as String),
    );
  }
}
