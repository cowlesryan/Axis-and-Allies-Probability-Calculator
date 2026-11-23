param([string]$Path)
Add-Type -ReferencedAssemblies System.Windows.Forms, System.Drawing -TypeDefinition @"
using System;
using System.Drawing;
using System.Drawing.Imaging;
using System.Runtime.InteropServices;
using System.Windows.Forms;

public class OverlayTest : Form {
    [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
    [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] static extern int GetWindowThreadProcessId(IntPtr h, out int pid);

    string outPath;
    public string ForegroundBefore = "", ForegroundAfter = "";

    protected override CreateParams CreateParams {
        get {
            CreateParams cp = base.CreateParams;
            // LAYERED | TRANSPARENT (click-through) | TOOLWINDOW (no taskbar) | NOACTIVATE (never steals focus)
            cp.ExStyle |= 0x00080000 | 0x00000020 | 0x00000080 | 0x08000000;
            return cp;
        }
    }
    protected override bool ShowWithoutActivation { get { return true; } }

    static string FgName() {
        int pid; GetWindowThreadProcessId(GetForegroundWindow(), out pid);
        try { return System.Diagnostics.Process.GetProcessById(pid).ProcessName; } catch { return "?"; }
    }

    public OverlayTest(string p) {
        outPath = p;
        FormBorderStyle = FormBorderStyle.None;
        TopMost = true; ShowInTaskbar = false;
        StartPosition = FormStartPosition.Manual;
        Bounds = new Rectangle(1740, 330, 620, 360);
        BackColor = Color.FromArgb(20, 20, 24);
        Opacity = 0.88;
        Label l = new Label();
        l.Dock = DockStyle.Fill; l.ForeColor = Color.White;
        l.Font = new Font("Consolas", 18, FontStyle.Bold);
        l.Text = "OVERLAY TEST\r\nAttacker wins: 12.34%\r\nDefender wins: 85.00%\r\n(click-through, no focus)";
        Controls.Add(l);
        Timer t = new Timer(); t.Interval = 1500;
        t.Tick += delegate {
            t.Stop();
            ForegroundAfter = FgName();
            Rectangle b = Screen.PrimaryScreen.Bounds;
            using (Bitmap bmp = new Bitmap(b.Width, b.Height))
            using (Graphics g = Graphics.FromImage(bmp)) {
                g.CopyFromScreen(b.Location, Point.Empty, b.Size);
                bmp.Save(outPath, ImageFormat.Png);
            }
            Close();
        };
        Shown += delegate { ForegroundBefore = FgName(); t.Start(); };
    }
    public static void Main2(string p) {
        SetProcessDPIAware();
        OverlayTest f = new OverlayTest(p);
        Application.EnableVisualStyles();
        Application.Run(f);
        Console.WriteLine("foreground before overlay shown: " + f.ForegroundBefore);
        Console.WriteLine("foreground after overlay shown : " + f.ForegroundAfter);
    }
}
"@
[OverlayTest]::Main2($Path)
"saved $Path"
