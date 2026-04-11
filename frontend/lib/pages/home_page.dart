import 'package:flutter/material.dart';

import '../services/app_state.dart';
import '../services/engine_client.dart';
import '../theme/app_colors.dart';
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
  int _index = 1; // start on the Screening tab — it's the core flow
  _EngineHealth _health = _EngineHealth.checking;
  String _engineLabel = '检查中…';

  static const _titles = ['被试管理', '早筛挑战', '眼控训练', '历史报告'];

  @override
  void initState() {
    super.initState();
    _pingEngine();
  }

  Future<void> _pingEngine() async {
    setState(() {
      _health = _EngineHealth.checking;
      _engineLabel = '检查中…';
    });
    try {
      final h = await widget.engine.health();
      if (!mounted) return;
      setState(() {
        _health = _EngineHealth.online;
        _engineLabel = '${h['service']} v${h['version']}';
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _health = _EngineHealth.offline;
        _engineLabel = '未连接';
      });
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
        titleSpacing: 20,
        title: Row(
          children: [
            Container(
              width: 42,
              height: 42,
              decoration: BoxDecoration(
                color: AppColors.primary,
                borderRadius: BorderRadius.circular(14),
              ),
              child: const Icon(Icons.remove_red_eye_rounded,
                  color: Colors.white, size: 24),
            ),
            const SizedBox(width: 12),
            Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                const Text(
                  'ADHD 早筛与训练',
                  style: TextStyle(
                    fontSize: 18,
                    fontWeight: FontWeight.w800,
                    color: AppColors.onSurface,
                  ),
                ),
                Text(
                  _titles[_index],
                  style: const TextStyle(
                    fontSize: 12,
                    color: AppColors.onSurfaceMuted,
                    fontWeight: FontWeight.w500,
                  ),
                ),
              ],
            ),
          ],
        ),
        actions: [
          Padding(
            padding: const EdgeInsets.only(right: 16),
            child: Center(
              child: _EngineStatusChip(
                health: _health,
                label: _engineLabel,
                onTap: _pingEngine,
              ),
            ),
          ),
        ],
      ),
      body: IndexedStack(index: _index, children: pages),
      bottomNavigationBar: Container(
        decoration: const BoxDecoration(
          border: Border(
            top: BorderSide(color: AppColors.divider, width: 1),
          ),
        ),
        child: NavigationBar(
          selectedIndex: _index,
          onDestinationSelected: (i) => setState(() => _index = i),
          destinations: const [
            NavigationDestination(
              icon: Icon(Icons.child_care_outlined),
              selectedIcon: Icon(Icons.child_care_rounded),
              label: '被试',
            ),
            NavigationDestination(
              icon: Icon(Icons.rocket_launch_outlined),
              selectedIcon: Icon(Icons.rocket_launch_rounded),
              label: '早筛',
            ),
            NavigationDestination(
              icon: Icon(Icons.sports_esports_outlined),
              selectedIcon: Icon(Icons.sports_esports_rounded),
              label: '训练',
            ),
            NavigationDestination(
              icon: Icon(Icons.bar_chart_outlined),
              selectedIcon: Icon(Icons.bar_chart_rounded),
              label: '报告',
            ),
          ],
        ),
      ),
    );
  }
}


enum _EngineHealth { checking, online, offline }


class _EngineStatusChip extends StatelessWidget {
  final _EngineHealth health;
  final String label;
  final VoidCallback onTap;

  const _EngineStatusChip({
    required this.health,
    required this.label,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    final (bg, fg, dot) = switch (health) {
      _EngineHealth.online => (
          AppColors.secondaryContainer,
          AppColors.onSecondaryContainer,
          AppColors.secondary,
        ),
      _EngineHealth.offline => (
          AppColors.errorContainer,
          AppColors.onErrorContainer,
          AppColors.error,
        ),
      _EngineHealth.checking => (
          AppColors.surfaceContainerHigh,
          AppColors.onSurfaceMuted,
          AppColors.onSurfaceMuted,
        ),
    };

    return Material(
      color: Colors.transparent,
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(20),
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
          decoration: BoxDecoration(
            color: bg,
            borderRadius: BorderRadius.circular(20),
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Container(
                width: 8,
                height: 8,
                decoration: BoxDecoration(
                  color: dot,
                  shape: BoxShape.circle,
                ),
              ),
              const SizedBox(width: 8),
              Text(
                label,
                style: TextStyle(
                  fontSize: 12,
                  fontWeight: FontWeight.w600,
                  color: fg,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
