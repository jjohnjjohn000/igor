# main.py
import faulthandler
faulthandler.enable()
import signal
import os
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib

# Import des nouveaux modules
from igor_window import AgentWindow

if __name__ == "__main__":
    win = AgentWindow()
    PID_FILE = "/tmp/igor_agent.pid"
    
    # Handler signal
    def sig_h(s, f):
        # Save the focused window before we take focus
        GLib.idle_add(win._save_focused_window)
        GLib.idle_add(win.start_listening)
    
    try:
        with open(PID_FILE, 'w') as f: 
            f.write(str(os.getpid()))
    except: 
        pass
    
    signal.signal(signal.SIGUSR1, sig_h)
    
    try: 
        Gtk.main()
    except KeyboardInterrupt: 
        pass
    finally:
        # Nettoyage subprocess
        if hasattr(win, 'wake_detector_process') and win.wake_detector_process:
            if win.wake_detector_process.poll() is None:
                print("  [CLEANUP] Arrêt subprocess final...", flush=True)
                try:
                    win.wake_detector_process.terminate()
                    win.wake_detector_process.wait(timeout=1)
                except:
                    win.wake_detector_process.kill()
        
        if os.path.exists(PID_FILE): 
            os.remove(PID_FILE)
        
        print("  [CLEANUP] ✅ Application fermée proprement", flush=True)