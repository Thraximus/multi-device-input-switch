package online.thrax.switcher;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/** Start the connection after a reboot or an app update. */
public final class BootReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context ctx, Intent intent) {
        if (new HubState(ctx).paired()) AgentService.start(ctx);
    }
}
