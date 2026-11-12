import android.os.SystemClock;
import android.view.InputDevice;
import android.view.InputEvent;
import android.view.MotionEvent;
import java.lang.reflect.Method;

// ADB-only timing harness. Never linked into the application.
public final class ScrollInput {
  public static void main(String[] args) throws Exception {
    float x1 = Float.parseFloat(args[0]), y1 = Float.parseFloat(args[1]);
    float x2 = Float.parseFloat(args[2]), y2 = Float.parseFloat(args[3]);
    int duration = Integer.parseInt(args[4]), hz = Integer.parseInt(args[5]);
    Class<?> type = Class.forName("android.hardware.input.InputManagerGlobal");
    Object manager = type.getMethod("getInstance").invoke(null);
    Method inject = type.getMethod("injectInputEvent", InputEvent.class, int.class);
    long down = SystemClock.uptimeMillis();
    send(manager, inject, down, down, MotionEvent.ACTION_DOWN, x1, y1);
    int count = 0;
    StringBuilder eventTimes = new StringBuilder();
    for (int n = 1; ; n++) {
      long target = down + Math.round(n * 1000.0 / hz);
      if (target >= down + duration) break;
      long wait = target - SystemClock.uptimeMillis();
      if (wait > 0) SystemClock.sleep(wait);
      long now = SystemClock.uptimeMillis();
      if (now >= down + duration) break;
      float f = (now - down) / (float) duration;
      send(manager, inject, down, now, MotionEvent.ACTION_MOVE,
          x1 + (x2 - x1) * f, y1 + (y2 - y1) * f);
      if (count > 0) eventTimes.append(",");
      eventTimes.append(now * 1000);
      count++;
    }
    long wait = down + duration - SystemClock.uptimeMillis();
    if (wait > 0) SystemClock.sleep(wait);
    long up = SystemClock.uptimeMillis();
    send(manager, inject, down, up, MotionEvent.ACTION_UP, x2, y2);
    System.out.println("{\"injectionMode\":1,\"moveMicros\":[" + eventTimes + "],\"moves\":" + count + ",\"durationMs\":" + duration + ",\"targetHz\":" + hz + ",\"downMicros\":" + down * 1000 + ",\"upMicros\":" + up * 1000 + "}");
  }

  static void send(Object manager, Method inject, long down, long now,
                   int action, float x, float y) throws Exception {
    MotionEvent.PointerProperties property = new MotionEvent.PointerProperties();
    property.id = 0;
    property.toolType = MotionEvent.TOOL_TYPE_FINGER;
    MotionEvent.PointerCoords coord = new MotionEvent.PointerCoords();
    coord.x = x; coord.y = y; coord.pressure = action == MotionEvent.ACTION_UP ? 0 : 1; coord.size = 1;
    MotionEvent event = MotionEvent.obtain(down, now, action, 1,
        new MotionEvent.PointerProperties[]{property}, new MotionEvent.PointerCoords[]{coord},
        0, 0, 1, 1, 0, 0, InputDevice.SOURCE_TOUCHSCREEN, 0);
    try {
      if (!((Boolean) inject.invoke(manager, event, 1)))
        throw new IllegalStateException("Input injection rejected");
    } finally { event.recycle(); }
  }
}
