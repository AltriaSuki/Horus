import 'package:flutter/material.dart';

import '../../models/subject.dart';
import '../../services/app_state.dart';
import '../../services/engine_client.dart';
import '../../theme/app_colors.dart';
import 'running_page.dart';

/// Screening launch page — the core of the core flow.
///
/// Layout (top to bottom):
///   * Hero card with a friendly mascot + title + subtitle
///   * 3-step "what you'll do" guide with rounded icon badges
///   * Subject picker
///   * Big juicy-orange start button
///   * Collapsible "pre-flight" tips card
class ScreeningStartPage extends StatefulWidget {
  final EngineClient engine;
  final AppState state;
  const ScreeningStartPage({
    super.key,
    required this.engine,
    required this.state,
  });

  @override
  State<ScreeningStartPage> createState() => _ScreeningStartPageState();
}

class _ScreeningStartPageState extends State<ScreeningStartPage> {
  Subject? _selected;

  Future<void> _start() async {
    final subj = _selected;
    if (subj == null) return;
    try {
      final session = await widget.engine.startSession(
        subjectId: subj.id,
        kind: 'screening',
        mode: 'sternberg',
      );
      if (!mounted) return;
      Navigator.of(context).push(MaterialPageRoute(
        builder: (_) => ScreeningRunningPage(
          engine: widget.engine,
          sessionId: session.id,
          subjectName: subj.displayName,
        ),
      ));
    } catch (e) {
      if (!mounted) return;
      showDialog(
        context: context,
        builder: (_) => AlertDialog(
          title: const Text('启动失败'),
          content: Text(e.toString()),
          actions: [
            TextButton(
              onPressed: () => Navigator.of(context).pop(),
              child: const Text('好的'),
            ),
          ],
        ),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    return SingleChildScrollView(
      padding: const EdgeInsets.fromLTRB(20, 8, 20, 32),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          const _HeroCard(),
          const SizedBox(height: 24),

          // ===== Three-step explainer ==========================================
          Text('闯关 3 步走', style: Theme.of(context).textTheme.titleLarge),
          const SizedBox(height: 12),
          const _StepCard(
            number: '1',
            color: AppColors.secondary,
            icon: Icons.chair_outlined,
            title: '坐好放松',
            subtitle: '找个安静的地方，坐直，离屏幕 50 厘米左右',
          ),
          const SizedBox(height: 10),
          const _StepCard(
            number: '2',
            color: AppColors.primary,
            icon: Icons.touch_app_outlined,
            title: '点点圆点',
            subtitle: '按顺序点亮屏幕上的 13 个蓝色圆圈，完成校准',
          ),
          const SizedBox(height: 10),
          const _StepCard(
            number: '3',
            color: AppColors.tertiary,
            icon: Icons.videogame_asset_outlined,
            title: '记圆点、按按键',
            subtitle: '记住屏幕上出现过的圆点位置，看到熟悉的按 F，新的按 J',
          ),
          const SizedBox(height: 28),

          // ===== Subject picker ================================================
          Text('选谁来闯关？', style: Theme.of(context).textTheme.titleLarge),
          const SizedBox(height: 12),
          ListenableBuilder(
            listenable: widget.state,
            builder: (context, _) {
              if (widget.state.loading && widget.state.subjects.isEmpty) {
                return const Padding(
                  padding: EdgeInsets.all(16),
                  child: Center(child: CircularProgressIndicator()),
                );
              }
              if (widget.state.error != null && widget.state.subjects.isEmpty) {
                return _InfoCard(
                  icon: Icons.error_outline,
                  iconColor: AppColors.error,
                  message: '加载被试失败: ${widget.state.error}',
                );
              }
              final subjects = widget.state.subjects;
              if (subjects.isEmpty) {
                return const _InfoCard(
                  icon: Icons.group_add_outlined,
                  iconColor: AppColors.secondary,
                  message: '请先到"被试"标签添加一个小朋友',
                );
              }
              if (_selected != null &&
                  !subjects.any((s) => s.id == _selected!.id)) {
                _selected = null;
              }
              return _SubjectPicker(
                subjects: subjects,
                selected: _selected,
                onChanged: (v) => setState(() => _selected = v),
              );
            },
          ),
          const SizedBox(height: 32),

          // ===== BIG START BUTTON ==============================================
          _StartButton(
            enabled: _selected != null,
            onPressed: _start,
          ),
          const SizedBox(height: 20),

          // ===== Pre-flight tips ===============================================
          const _TipsCard(),
        ],
      ),
    );
  }
}

// ───────────────────────────────────────────────────────────────────────────
// Hero card at the top
// ───────────────────────────────────────────────────────────────────────────


class _HeroCard extends StatelessWidget {
  const _HeroCard();

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(24),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(28),
        gradient: const LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: [
            Color(0xFFFFE4CC),
            Color(0xFFFFF0C2),
          ],
        ),
      ),
      child: Row(
        children: [
          // Mascot — a warm circular badge with an eye icon
          Container(
            width: 80,
            height: 80,
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              color: AppColors.primary,
              boxShadow: [
                BoxShadow(
                  color: AppColors.primary.withValues(alpha: 0.25),
                  blurRadius: 20,
                  offset: const Offset(0, 8),
                ),
              ],
            ),
            child: const Icon(Icons.visibility_outlined,
                color: Colors.white, size: 44),
          ),
          const SizedBox(width: 20),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: [
                Text('视觉记忆挑战',
                    style: Theme.of(context).textTheme.headlineMedium),
                const SizedBox(height: 4),
                Text(
                  '小游戏 · 约 25 分钟',
                  style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                        color: AppColors.onSurfaceMuted,
                      ),
                ),
                const SizedBox(height: 8),
                Container(
                  padding: const EdgeInsets.symmetric(
                      horizontal: 10, vertical: 4),
                  decoration: BoxDecoration(
                    color: AppColors.secondaryContainer,
                    borderRadius: BorderRadius.circular(14),
                  ),
                  child: Text(
                    '🏆 完成后会得到一份专属报告',
                    style: Theme.of(context).textTheme.labelMedium?.copyWith(
                          color: AppColors.onSecondaryContainer,
                          fontWeight: FontWeight.w600,
                        ),
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}


// ───────────────────────────────────────────────────────────────────────────
// Step explainer card
// ───────────────────────────────────────────────────────────────────────────


class _StepCard extends StatelessWidget {
  final String number;
  final Color color;
  final IconData icon;
  final String title;
  final String subtitle;

  const _StepCard({
    required this.number,
    required this.color,
    required this.icon,
    required this.title,
    required this.subtitle,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(18),
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: AppColors.divider),
      ),
      child: Row(
        children: [
          Stack(
            alignment: Alignment.center,
            children: [
              Container(
                width: 56,
                height: 56,
                decoration: BoxDecoration(
                  color: color.withValues(alpha: 0.18),
                  borderRadius: BorderRadius.circular(18),
                ),
                child: Icon(icon, color: color, size: 28),
              ),
              Positioned(
                top: 0,
                right: 0,
                child: Container(
                  width: 22,
                  height: 22,
                  decoration: BoxDecoration(
                    color: color,
                    shape: BoxShape.circle,
                    border:
                        Border.all(color: AppColors.surface, width: 2.5),
                  ),
                  child: Center(
                    child: Text(number,
                        style: const TextStyle(
                          color: Colors.white,
                          fontSize: 11,
                          fontWeight: FontWeight.w800,
                        )),
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(width: 16),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: [
                Text(title,
                    style: Theme.of(context).textTheme.titleMedium),
                const SizedBox(height: 2),
                Text(subtitle,
                    style: Theme.of(context).textTheme.bodySmall),
              ],
            ),
          ),
        ],
      ),
    );
  }
}


// ───────────────────────────────────────────────────────────────────────────
// Subject picker
// ───────────────────────────────────────────────────────────────────────────


class _SubjectPicker extends StatelessWidget {
  final List<Subject> subjects;
  final Subject? selected;
  final ValueChanged<Subject?> onChanged;

  const _SubjectPicker({
    required this.subjects,
    required this.selected,
    required this.onChanged,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
      decoration: BoxDecoration(
        color: AppColors.surfaceContainer,
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: AppColors.divider),
      ),
      child: DropdownButtonHideUnderline(
        child: DropdownButton<Subject>(
          isExpanded: true,
          value: selected,
          hint: const Padding(
            padding: EdgeInsets.symmetric(vertical: 12),
            child: Text('请选择一位小朋友'),
          ),
          icon: const Icon(Icons.arrow_drop_down,
              color: AppColors.onSurfaceMuted),
          items: subjects.map((s) {
            final letter =
                s.displayName.isNotEmpty ? s.displayName[0] : s.id[0];
            return DropdownMenuItem(
              value: s,
              child: Padding(
                padding: const EdgeInsets.symmetric(vertical: 8),
                child: Row(
                  children: [
                    CircleAvatar(
                      radius: 20,
                      backgroundColor: AppColors.primaryContainer,
                      child: Text(
                        letter,
                        style: const TextStyle(
                          color: AppColors.primary,
                          fontWeight: FontWeight.w800,
                          fontSize: 16,
                        ),
                      ),
                    ),
                    const SizedBox(width: 12),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          Text(s.displayName,
                              style: const TextStyle(
                                fontSize: 16,
                                fontWeight: FontWeight.w600,
                                color: AppColors.onSurface,
                              )),
                          Text('ID: ${s.id}',
                              style: const TextStyle(
                                fontSize: 12,
                                color: AppColors.onSurfaceMuted,
                              )),
                        ],
                      ),
                    ),
                  ],
                ),
              ),
            );
          }).toList(),
          onChanged: onChanged,
        ),
      ),
    );
  }
}


// ───────────────────────────────────────────────────────────────────────────
// Big start button
// ───────────────────────────────────────────────────────────────────────────


class _StartButton extends StatelessWidget {
  final bool enabled;
  final VoidCallback onPressed;

  const _StartButton({required this.enabled, required this.onPressed});

  @override
  Widget build(BuildContext context) {
    return Container(
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(32),
        boxShadow: enabled
            ? [
                BoxShadow(
                  color: AppColors.primary.withValues(alpha: 0.35),
                  blurRadius: 24,
                  offset: const Offset(0, 8),
                ),
              ]
            : [],
      ),
      child: FilledButton(
        onPressed: enabled ? onPressed : null,
        style: FilledButton.styleFrom(
          minimumSize: const Size.fromHeight(68),
          backgroundColor: AppColors.primary,
          disabledBackgroundColor: AppColors.surfaceContainerHigh,
          disabledForegroundColor: AppColors.onSurfaceMuted,
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(32),
          ),
        ),
        child: Row(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            const Icon(Icons.rocket_launch_rounded, size: 26),
            const SizedBox(width: 10),
            Text(
              enabled ? '开始闯关！' : '先选一位小朋友',
              style: const TextStyle(
                fontSize: 19,
                fontWeight: FontWeight.w800,
                letterSpacing: 1,
              ),
            ),
          ],
        ),
      ),
    );
  }
}


// ───────────────────────────────────────────────────────────────────────────
// Collapsible tips card
// ───────────────────────────────────────────────────────────────────────────


class _TipsCard extends StatelessWidget {
  const _TipsCard();

  @override
  Widget build(BuildContext context) {
    return Container(
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: AppColors.divider),
      ),
      child: Theme(
        data: Theme.of(context).copyWith(
          dividerColor: Colors.transparent,
        ),
        child: ExpansionTile(
          leading: const Icon(Icons.lightbulb_outline,
              color: AppColors.tertiary),
          title: const Text('开始前的小提示',
              style: TextStyle(
                fontWeight: FontWeight.w700,
                fontSize: 16,
              )),
          tilePadding: const EdgeInsets.symmetric(horizontal: 20),
          childrenPadding:
              const EdgeInsets.fromLTRB(20, 0, 20, 16),
          expandedAlignment: Alignment.topLeft,
          expandedCrossAxisAlignment: CrossAxisAlignment.start,
          children: [
            _tipLine(Icons.wb_sunny_outlined, '环境光线柔和，不要有强光照在脸上'),
            _tipLine(Icons.videocam_outlined, '第一次启动时需要允许摄像头访问'),
            _tipLine(Icons.airline_seat_recline_normal,
                '坐姿端正，离屏幕约 50 厘米'),
            _tipLine(Icons.do_not_disturb_on_outlined,
                '任务期间会全屏，按 ESC 可以中止'),
          ],
        ),
      ),
    );
  }

  Widget _tipLine(IconData icon, String text) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(icon, size: 18, color: AppColors.onSurfaceMuted),
          const SizedBox(width: 10),
          Expanded(
            child: Text(text,
                style: const TextStyle(
                  fontSize: 14,
                  color: AppColors.onSurfaceMuted,
                  height: 1.5,
                )),
          ),
        ],
      ),
    );
  }
}


// ───────────────────────────────────────────────────────────────────────────
// Small info card (shared with running page)
// ───────────────────────────────────────────────────────────────────────────


class _InfoCard extends StatelessWidget {
  final IconData icon;
  final Color iconColor;
  final String message;

  const _InfoCard({
    required this.icon,
    required this.iconColor,
    required this.message,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(18),
      decoration: BoxDecoration(
        color: AppColors.surfaceContainer,
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: AppColors.divider),
      ),
      child: Row(
        children: [
          Icon(icon, color: iconColor),
          const SizedBox(width: 12),
          Expanded(
            child: Text(
              message,
              style: const TextStyle(
                fontSize: 14,
                color: AppColors.onSurface,
              ),
            ),
          ),
        ],
      ),
    );
  }
}
