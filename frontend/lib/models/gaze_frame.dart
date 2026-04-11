/// One sample from the `WS /gaze` stream.
class GazeFrame {
  final String sessionId;
  final double t; // monotonic seconds since worker start
  final double x; // screen px (NaN if invalid)
  final double y;
  final double pupil;
  final bool valid;
  final int fps;

  const GazeFrame({
    required this.sessionId,
    required this.t,
    required this.x,
    required this.y,
    required this.pupil,
    required this.valid,
    required this.fps,
  });

  factory GazeFrame.fromJson(Map<String, dynamic> json) {
    double parseNum(dynamic v) {
      if (v == null) return double.nan;
      if (v is num) return v.toDouble();
      return double.nan;
    }

    return GazeFrame(
      sessionId: json['session_id'] as String,
      t: parseNum(json['t']),
      x: parseNum(json['x']),
      y: parseNum(json['y']),
      pupil: parseNum(json['pupil']),
      valid: (json['valid'] as bool?) ?? false,
      fps: (json['fps'] as int?) ?? 30,
    );
  }
}
