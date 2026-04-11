import 'package:flutter/material.dart';

import '../services/app_state.dart';
import '../services/engine_client.dart';
import 'reports/history_page.dart';
import 'screening/start_page.dart';
import 'subjects/subjects_list_page.dart';
import 'training/training_page.dart';

/// Top-level shell with a 4-tab bottom navigation:
///   被试 / 早筛 / 训练 / 报告
class HomePage extends StatefulWidget {
  final EngineClient engine;
  final AppState state;
  const HomePage({super.key, required this.engine, required this.state});

  @override
  State<HomePage> createState() => _HomePageState();
}

class _HomePageState extends State<HomePage> {
  int _index = 0;
  String _engineStatus = '检查中…';

  @override
  void initState() {
    super.initState();
    _pingEngine();
  }

  Future<void> _pingEngine() async {
    try {
      final h = await widget.engine.health();
      if (!mounted) return;
      setState(() {
        _engineStatus = '已连接 · ${h['service']} v${h['version']}';
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _engineStatus = '未连接 · ${widget.engine.baseUrl}');
    }
  }

  @override
  Widget build(BuildContext context) {
    final pages = [
      SubjectsListPage(engine: widget.engine, state: widget.state),
      ScreeningStartPage(engine: widget.engine, state: widget.state),
      TrainingPage(engine: widget.engine, state: widget.state),
      ReportsHistoryPage(engine: widget.engine, state: widget.state),
    ];

    return Scaffold(
      appBar: AppBar(
        title: const Text('ADHD 早筛与训练系统'),
        actions: [
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16),
            child: Center(
              child: GestureDetector(
                onTap: _pingEngine,
                child: Text(
                  _engineStatus,
                  style: Theme.of(context).textTheme.bodySmall,
                ),
              ),
            ),
          ),
        ],
      ),
      body: IndexedStack(index: _index, children: pages),
      bottomNavigationBar: NavigationBar(
        selectedIndex: _index,
        onDestinationSelected: (i) => setState(() => _index = i),
        destinations: const [
          NavigationDestination(
            icon: Icon(Icons.people_outline),
            selectedIcon: Icon(Icons.people),
            label: '被试',
          ),
          NavigationDestination(
            icon: Icon(Icons.psychology_outlined),
            selectedIcon: Icon(Icons.psychology),
            label: '早筛',
          ),
          NavigationDestination(
            icon: Icon(Icons.sports_esports_outlined),
            selectedIcon: Icon(Icons.sports_esports),
            label: '训练',
          ),
          NavigationDestination(
            icon: Icon(Icons.assessment_outlined),
            selectedIcon: Icon(Icons.assessment),
            label: '报告',
          ),
        ],
      ),
    );
  }
}
