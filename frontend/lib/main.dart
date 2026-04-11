import 'package:flutter/material.dart';

import 'app.dart';
import 'services/app_state.dart';
import 'services/engine_client.dart';

void main() {
  // The engine is expected to be running locally before the app launches.
  // For desktop builds the user starts it manually with:
  //   PYTHONPATH=engine .venv/bin/python -m adhd_engine.server
  final client = EngineClient();
  final state = AppState(client);
  // Kick off an initial subject fetch — pages that listen to AppState will
  // pick up the result as soon as it lands.
  state.refreshSubjects();
  runApp(AdhdShellApp(engine: client, state: state));
}
