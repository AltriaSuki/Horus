/// One message from the `WS /events` stream. Generic envelope:
///   {"type": "<name>", "session_id": "...", ...}
class TaskEvent {
  final String type;
  final String? sessionId;
  final Map<String, dynamic> payload;

  const TaskEvent({
    required this.type,
    this.sessionId,
    this.payload = const {},
  });

  factory TaskEvent.fromJson(Map<String, dynamic> json) {
    final type = json['type'] as String;
    final sessionId = json['session_id'] as String?;
    final payload = Map<String, dynamic>.from(json)
      ..remove('type')
      ..remove('session_id');
    return TaskEvent(type: type, sessionId: sessionId, payload: payload);
  }

  /// Sternberg trial events carry a 1-based trial number.
  int? get trialNum {
    final v = payload['trial_num'];
    if (v is int) return v;
    if (v is num) return v.toInt();
    return null;
  }

  int? get totalTrials {
    final v = payload['total_trials'];
    if (v is int) return v;
    if (v is num) return v.toInt();
    return null;
  }
}
