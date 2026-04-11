/// Mirrors `adhd_engine.api.sessions.SessionOut`.
class ScreeningSession {
  final String id;
  final String subjectId;
  final String kind; // 'screening' | 'game'
  final String mode; // 'sternberg' | 'track' | 'game_*'
  final String status; // pending|running|completed|failed|cancelled
  final String? errorMessage;

  const ScreeningSession({
    required this.id,
    required this.subjectId,
    required this.kind,
    required this.mode,
    required this.status,
    this.errorMessage,
  });

  factory ScreeningSession.fromJson(Map<String, dynamic> json) {
    return ScreeningSession(
      id: json['id'] as String,
      subjectId: json['subject_id'] as String,
      kind: json['kind'] as String,
      mode: json['mode'] as String,
      status: json['status'] as String,
      errorMessage: json['error_message'] as String?,
    );
  }

  bool get isTerminal =>
      status == 'completed' || status == 'failed' || status == 'cancelled';
}
