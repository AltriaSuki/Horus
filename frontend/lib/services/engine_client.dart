/// REST + WebSocket client for the ADHD engine.
///
/// One instance per app run. Holds the engine base URL and provides:
///
/// * REST helpers for subjects / sessions / reports / games (HTTP via `http`)
/// * Two ``Stream`` factories for the live `/gaze` and `/events` channels
///   (`web_socket_channel`).
///
/// All endpoints are documented in `engine/docs/ws_protocol.md` and the
/// FastAPI router files under `engine/adhd_engine/api/`.
library;

import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;
import 'package:web_socket_channel/web_socket_channel.dart';

import '../models/gaze_frame.dart';
import '../models/report.dart';
import '../models/session.dart';
import '../models/subject.dart';
import '../models/task_event.dart';

class EngineApiException implements Exception {
  final int statusCode;
  final String body;
  EngineApiException(this.statusCode, this.body);

  @override
  String toString() => 'EngineApiException($statusCode): $body';
}

class EngineClient {
  /// Base URL of the engine, e.g. ``http://127.0.0.1:8765``.
  final Uri baseUrl;
  final http.Client _http;

  EngineClient({Uri? baseUrl, http.Client? httpClient})
      : baseUrl = baseUrl ?? Uri.parse('http://127.0.0.1:8765'),
        _http = httpClient ?? http.Client();

  void close() => _http.close();

  // ---------------------------------------------------------------------
  // Health
  // ---------------------------------------------------------------------

  Future<Map<String, dynamic>> health() async {
    final r = await _http.get(baseUrl);
    _check(r);
    return jsonDecode(r.body) as Map<String, dynamic>;
  }

  // ---------------------------------------------------------------------
  // Subjects
  // ---------------------------------------------------------------------

  Future<Subject> createSubject(Subject subject) async {
    final r = await _http.post(
      baseUrl.resolve('/subjects'),
      headers: const {'Content-Type': 'application/json'},
      body: jsonEncode(subject.toJson()),
    );
    _check(r);
    return Subject.fromJson(jsonDecode(r.body) as Map<String, dynamic>);
  }

  Future<List<Subject>> listSubjects() async {
    final r = await _http.get(baseUrl.resolve('/subjects'));
    _check(r);
    final raw = jsonDecode(r.body) as List<dynamic>;
    return raw
        .map((e) => Subject.fromJson(e as Map<String, dynamic>))
        .toList(growable: false);
  }

  Future<Subject> getSubject(String id) async {
    final r = await _http.get(baseUrl.resolve('/subjects/$id'));
    _check(r);
    return Subject.fromJson(jsonDecode(r.body) as Map<String, dynamic>);
  }

  /// History of sessions for a given subject.
  ///
  /// Returns a list of dicts in the shape produced by
  /// `engine/api/subjects.SessionSummary`. The Flutter side decodes them
  /// into the local `_SessionRow` model in `pages/reports/history_page.dart`.
  Future<List<Map<String, dynamic>>> listSubjectSessions(String subjectId) async {
    final r = await _http.get(baseUrl.resolve('/subjects/$subjectId/sessions'));
    _check(r);
    final raw = jsonDecode(r.body) as List<dynamic>;
    return raw.cast<Map<String, dynamic>>();
  }

  // ---------------------------------------------------------------------
  // Sessions
  // ---------------------------------------------------------------------

  Future<ScreeningSession> startSession({
    required String subjectId,
    String kind = 'screening',
    String mode = 'sternberg',
  }) async {
    final r = await _http.post(
      baseUrl.resolve('/sessions'),
      headers: const {'Content-Type': 'application/json'},
      body: jsonEncode({
        'subject_id': subjectId,
        'kind': kind,
        'mode': mode,
      }),
    );
    _check(r);
    return ScreeningSession.fromJson(
        jsonDecode(r.body) as Map<String, dynamic>);
  }

  Future<ScreeningSession> getSession(String sessionId) async {
    final r = await _http.get(baseUrl.resolve('/sessions/$sessionId'));
    _check(r);
    return ScreeningSession.fromJson(
        jsonDecode(r.body) as Map<String, dynamic>);
  }

  Future<ScreeningSession> cancelSession(String sessionId) async {
    final r = await _http.post(baseUrl.resolve('/sessions/$sessionId/cancel'));
    _check(r);
    return ScreeningSession.fromJson(
        jsonDecode(r.body) as Map<String, dynamic>);
  }

  /// Returns null if no report has been written yet (HTTP 404).
  Future<AdhdReport?> getReport(String sessionId) async {
    final r = await _http.get(baseUrl.resolve('/sessions/$sessionId/report'));
    if (r.statusCode == 404) return null;
    _check(r);
    return AdhdReport.fromJson(jsonDecode(r.body) as Map<String, dynamic>);
  }

  // ---------------------------------------------------------------------
  // Games (placeholder)
  // ---------------------------------------------------------------------

  Future<int> createGameRun({
    required String sessionId,
    required String gameName,
  }) async {
    final r = await _http.post(
      baseUrl.resolve('/games/runs'),
      headers: const {'Content-Type': 'application/json'},
      body: jsonEncode({'session_id': sessionId, 'game_name': gameName}),
    );
    _check(r);
    final body = jsonDecode(r.body) as Map<String, dynamic>;
    return body['id'] as int;
  }

  Future<void> finishGameRun({
    required int runId,
    int? score,
    Map<String, dynamic>? payload,
  }) async {
    final r = await _http.patch(
      baseUrl.resolve('/games/runs/$runId'),
      headers: const {'Content-Type': 'application/json'},
      body: jsonEncode({
        if (score != null) 'score': score,
        if (payload != null) 'payload': payload,
      }),
    );
    _check(r);
  }

  // ---------------------------------------------------------------------
  // WebSocket streams
  // ---------------------------------------------------------------------

  Uri _wsUri(String path) {
    final scheme = baseUrl.scheme == 'https' ? 'wss' : 'ws';
    return baseUrl.replace(scheme: scheme, path: path);
  }

  /// Subscribe to live gaze frames. Each yielded frame is a `GazeFrame`.
  ///
  /// The returned stream is **broadcast** so multiple widgets can listen
  /// (e.g. a heatmap + a status indicator). Disconnect by calling `close()`
  /// on the future returned by ``cancelSubscription`` or by simply having
  /// no more listeners — the underlying socket closes.
  Stream<GazeFrame> gazeStream() {
    final channel = WebSocketChannel.connect(_wsUri('/gaze'));
    return channel.stream
        .map<GazeFrame>((data) {
          if (data is String) {
            final json = jsonDecode(data) as Map<String, dynamic>;
            return GazeFrame.fromJson(json);
          }
          throw FormatException('unexpected ws payload: $data');
        })
        .asBroadcastStream();
  }

  /// Subscribe to high-level task events.
  Stream<TaskEvent> eventStream() {
    final channel = WebSocketChannel.connect(_wsUri('/events'));
    return channel.stream
        .map<TaskEvent>((data) {
          if (data is String) {
            final json = jsonDecode(data) as Map<String, dynamic>;
            return TaskEvent.fromJson(json);
          }
          throw FormatException('unexpected ws payload: $data');
        })
        .asBroadcastStream();
  }

  // ---------------------------------------------------------------------
  // Internal
  // ---------------------------------------------------------------------

  void _check(http.Response r) {
    if (r.statusCode >= 400) {
      throw EngineApiException(r.statusCode, r.body);
    }
  }
}
