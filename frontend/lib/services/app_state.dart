/// Process-wide reactive state for the front-end.
///
/// Holds the cached list of subjects so multiple pages stay in sync without
/// each one re-fetching independently. Pages that read subjects use a
/// `ListenableBuilder` (or `AnimatedBuilder`) on this object — when a new
/// subject is created, every listening page rebuilds with the fresh list.
///
/// Why this exists: `HomePage` uses an `IndexedStack` so each tab's `State`
/// is preserved across switches. A naive `late Future<List<Subject>>` cached
/// in `initState()` therefore becomes stale forever once the user adds a
/// subject in another tab. Centralising the cache here eliminates that
/// staleness without making each tab re-fetch on every build.
library;

import 'package:flutter/foundation.dart';

import '../models/subject.dart';
import 'engine_client.dart';

class AppState extends ChangeNotifier {
  final EngineClient engine;

  List<Subject> _subjects = const [];
  bool _loading = false;
  String? _error;

  AppState(this.engine);

  List<Subject> get subjects => _subjects;
  bool get loading => _loading;
  String? get error => _error;

  /// Pull the latest list of subjects from the engine.
  Future<void> refreshSubjects() async {
    _loading = true;
    _error = null;
    notifyListeners();
    try {
      _subjects = await engine.listSubjects();
    } catch (e) {
      _error = e.toString();
    } finally {
      _loading = false;
      notifyListeners();
    }
  }

  /// Create a new subject and refresh the cache so every listening page
  /// updates immediately.
  Future<Subject> createSubject(Subject draft) async {
    final created = await engine.createSubject(draft);
    await refreshSubjects();
    return created;
  }
}
