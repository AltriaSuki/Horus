// Smoke test for the AdhdShell entry widget.
//
// We can't reach a real engine in unit-test land, so we point the EngineClient
// at an unused localhost port. The HomePage's `_pingEngine` will time out and
// the status text will say "未连接 …" — that's still a valid render and proves
// the widget tree builds.

import 'package:flutter_test/flutter_test.dart';

import 'package:adhd_shell/app.dart';
import 'package:adhd_shell/services/app_state.dart';
import 'package:adhd_shell/services/engine_client.dart';

void main() {
  testWidgets('AdhdShellApp builds the home scaffold', (tester) async {
    final engine = EngineClient(baseUrl: Uri.parse('http://127.0.0.1:1'));
    final state = AppState(engine);
    await tester.pumpWidget(AdhdShellApp(engine: engine, state: state));

    // Top app bar title is rendered
    expect(find.text('ADHD 早筛与训练系统'), findsOneWidget);

    // Bottom navigation has 4 destinations
    expect(find.text('被试'), findsOneWidget);
    expect(find.text('早筛'), findsOneWidget);
    expect(find.text('训练'), findsOneWidget);
    expect(find.text('报告'), findsOneWidget);

    engine.close();
  });
}
