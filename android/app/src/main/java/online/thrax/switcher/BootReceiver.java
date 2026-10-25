package online.thrax.switcher;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

import online.thrax.switcher.core.DeviceConfig;

/** Start the connection after a reboot or an app update, unless the user disconnected. */
public final class BootReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context ctx, Intent intent) {
        if (new HubState(ctx).paired() && !new DeviceConfig(ctx).getStopped()) AgentService.start(ctx);
    }
}
