import 'package:flutter/material.dart';

import 'pages/home_page.dart';
import 'services/app_state.dart';
import 'services/engine_client.dart';

class AdhdShellApp extends StatelessWidget {
  final EngineClient engine;
  final AppState state;

  const AdhdShellApp({super.key, required this.engine, required this.state});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'ADHD 早筛与训练',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        useMaterial3: true,
        colorScheme: ColorScheme.fromSeed(
          seedColor: const Color(0xFF2C6FBB),
          brightness: Brightness.light,
        ),
        fontFamily: 'PingFang SC',
      ),
      home: HomePage(engine: engine, state: state),
    );
  }
}
