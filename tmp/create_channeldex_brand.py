from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
import os

ROOT = Path.cwd()
OUT = ROOT / "output" / "channeldex-brand"
OUT.mkdir(parents=True, exist_ok=True)

# The quiet, purpose-built palette: blue indexes broadcast information, green is workflow,
# burgundy is emphasis/exception. Charcoal and ivory carry almost all normal UI usage.
IVORY = "#E9E1D3"; CHAR = "#1E1E1E"; BLUE = "#245181"; GREEN = "#456E46"; BURG = "#7A292A"; WARM = "#D6C8B5"; WHITE = "#F8F5EE"
F_BOLD = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
F_REG = "/System/Library/Fonts/Supplemental/Arial.ttf"
F_MONO = "/System/Library/Fonts/Supplemental/Courier New.ttf"
def font(path, size): return ImageFont.truetype(path, size)

def icon(draw, x, y, s, mono=False, bg=IVORY):
    # A framed record card: channel slot, catalog entry, television screen.
    dark = CHAR
    draw.rounded_rectangle((x,y,x+s,y+s), radius=int(s*.16), fill=dark)
    inn=int(s*.16); gap=int(s*.075); barh=int(s*.18)
    draw.rounded_rectangle((x+inn,y+inn,x+s-inn,y+s-inn), radius=int(s*.045), fill=bg)
    cols=[dark,dark,dark] if mono else [BLUE,GREEN,BURG]
    for i,c in enumerate(cols):
        yy=y+inn+gap+i*(barh+gap)
        draw.rounded_rectangle((x+inn+gap,yy,x+s-inn-gap,yy+barh),radius=int(s*.018),fill=c)

def draw_wordmark(draw, x, y, height, mono=False):
    # Purposefully close, strong technical grotesk wordmark with a colored Dex suffix.
    f=font(F_BOLD,height)
    first="Channel"; suffix="Dex"
    draw.text((x,y),first,font=f,fill=CHAR,anchor="la")
    firstw=draw.textlength(first,font=f)
    draw.text((x+firstw-4,y),suffix,font=f,fill=CHAR if mono else BURG,anchor="la")
    return int(firstw+draw.textlength(suffix,font=f)-4)

def save_primary(name, mono=False, lockup=False, descriptor=False):
    im=Image.new("RGB",(2100,700),IVORY); d=ImageDraw.Draw(im)
    icon(d,90,150,300,mono)
    ww=draw_wordmark(d,440,175,165,mono)
    if lockup:
        x=440; yy=375
        d.line((x,yy,x+ww,yy),fill=CHAR,width=7)
        d.text((x+ww+24,yy-21),"BY CAPITAL MEDIA SYSTEMS",font=font(F_BOLD,31),fill=CHAR,anchor="la")
    if descriptor:
        d.text((440,510),"T E L E V I S I O N   P R O G R A M M I N G   M A N A G E M E N T",font=font(F_MONO,23),fill=CHAR,anchor="la")
    # Corner register marks confirm system-like, not decorative, language.
    for xx in (30,1770): d.line((xx,30,xx+18 if xx==30 else xx-18,30),fill=WARM,width=2)
    im.save(OUT/name)

def save_favicon():
    im=Image.new("RGBA",(512,512),(0,0,0,0)); d=ImageDraw.Draw(im)
    icon(d,48,48,416,False,CHAR)
    im.save(OUT/"channeldex-favicon-color.png")

def save_app():
    im=Image.new("RGB",(1024,1024),CHAR); d=ImageDraw.Draw(im)
    # restrained paper inset evokes a carefully labeled physical software package
    d.rounded_rectangle((115,115,909,909),radius=145,fill=IVORY)
    icon(d,278,278,468,False,IVORY)
    d.text((512,825),"CHANNELDEX",font=font(F_MONO,30),fill=CHAR,anchor="ma")
    im.save(OUT/"channeldex-application-icon-color.png")

def save_corporate():
    im=Image.new("RGB",(1600,800),IVORY); d=ImageDraw.Draw(im)
    # Abstracted capitol drum + broadcast mast; intentionally not municipal seal art.
    cx=800
    d.rectangle((cx-180,290,cx+180,315),fill=BLUE)
    d.polygon([(cx-205,290),(cx,190),(cx+205,290)],fill=BLUE)
    d.rectangle((cx-125,135,cx+125,190),fill=BLUE)
    d.ellipse((cx-50,75,cx+50,165),fill=BLUE)
    d.rectangle((cx-7,40,cx+7,95),fill=BLUE)
    for xx in range(cx-140,cx+141,56): d.rectangle((xx,315,xx+24,400),fill=BLUE)
    d.rectangle((cx-185,400,cx+185,425),fill=BLUE)
    d.text((cx,465),"CAPITAL MEDIA SYSTEMS",font=font(F_BOLD,61),fill=CHAR,anchor="ma")
    d.text((cx,545),"P R O G R A M M I N G   A   B R I G H T E R   T O M O R R O W",font=font(F_MONO,22),fill=CHAR,anchor="ma")
    im.save(OUT/"capital-media-systems-corporate-mark.png")

save_primary("channeldex-color.png")
save_primary("channeldex-monochrome.png",True)
save_primary("channeldex-by-capital-media-systems-color.png",False,True)
save_primary("channeldex-by-capital-media-systems-monochrome.png",True,True)
save_primary("channeldex-full-lockup-color.png",False,True,True)
save_favicon(); save_app(); save_corporate()

# PDF guide: 8 carefully structured pages, letter landscape for legibility and easy print.
PDF=OUT/"ChannelDex_Brand_Guide.pdf"; W,H=letter; W,H=H,W
c=canvas.Canvas(str(PDF),pagesize=(W,H))
def rect(x,y,w,h,fill): c.setFillColor(fill); c.rect(x,y,w,h,fill=1,stroke=0)
def tx(x,y,s,size=12,bold=False,color=CHAR):
    c.setFillColor(color); c.setFont("Helvetica-Bold" if bold else "Helvetica",size); c.drawString(x,y,s)
def mono(x,y,s,size=9,color=CHAR): c.setFillColor(color); c.setFont("Courier",size); c.drawString(x,y,s)
def footer(n):
    c.setStrokeColor(WARM); c.line(48,34,W-48,34); mono(48,18,"CHANNELDEX / BRAND STANDARDS / 2026",8); mono(W-70,18,f"{n:02d}",8)
def title(kicker, heading, detail, n):
    rect(0,0,W,H,IVORY); mono(48,H-55,kicker,10); tx(48,H-120,heading,31,True); tx(48,H-150,detail,12); footer(n)
def image(path,x,y,w,h): c.drawImage(ImageReader(str(OUT/path)),x,y,width=w,height=h,preserveAspectRatio=True,mask='auto')

title("CHANNELDEX / IDENTITY PROGRAM", "A durable information identity.", "Brand guide for ChannelDex by Capital Media Systems. Proposed identity system.",1)
image("channeldex-full-lockup-color.png",52,170,690,230)
tx(52,156,"Television programming management for systems that need an authoritative record.",15,False)
mono(52,112,"A MODERN PRODUCT WITH THE DISCIPLINE OF INSTITUTIONAL COMPUTING.",10)
c.showPage()

title("01 / BRAND IDEA", "Positioning and character", "The identity should communicate organization before nostalgia.",2)
cols=[("COMPETENT","Clear authority. No theatrics."),("TECHNICAL","Structured, legible, precise."),("INSTITUTIONAL","Stable tools for durable records."),("QUIETLY DISTINCTIVE","A dry, knowing 1980s reference.")]
for i,(a,b) in enumerate(cols):
    x=55+(i%2)*350; y=430-(i//2)*155; rect(x,y,300,100,WHITE); mono(x+18,y+68,a,12); tx(x+18,y+37,b,11)
mono(55,190,"DO: catalog, frame, index, schedule, record, slot, signal.",10); mono(55,165,"DON'T: neon, arcade, cyberpunk, mascot, faux VHS, consumer SaaS gloss.",10)
c.showPage()

title("02 / PRIMARY MARK", "The framed record", "A single symbol connecting a broadcast screen, catalog card, and channel slot.",3)
image("channeldex-color.png",50,365,660,255); image("channeldex-monochrome.png",50,100,660,255)
mono(735,548,"COLOR",10); mono(735,510,"Use on ivory or white.",9); mono(735,483,"MONOCHROME",10); mono(735,445,"Use one ink only.",9)
mono(735,285,"CLEAR SPACE",10); tx(735,252,"Keep one bar-height",11); tx(735,234,"around all sides.",11)
c.showPage()

title("03 / LOCKUPS", "One product. One quiet parent.", "ChannelDex remains dominant; Capital Media Systems is an endorsing signature.",4)
image("channeldex-by-capital-media-systems-color.png",50,365,660,255); image("channeldex-by-capital-media-systems-monochrome.png",50,100,660,255)
mono(735,548,"ENDORSED LOCKUP",10); tx(735,510,"Use for product pages,",11); tx(735,492,"manuals, and launch screens.",11)
mono(735,285,"FULL LOCKUP",10); tx(735,252,"Add descriptor only where",11); tx(735,234,"context needs it.",11)
c.showPage()

title("04 / COLOR", "A restrained broadcast palette", "Charcoal and warm ivory are the system. Accent colors carry purposeful signals.",5)
sw=[(CHAR,"CHARCOAL","#1E1E1E"),(BLUE,"STATION BLUE","#245181"),(GREEN,"MONITOR GREEN","#456E46"),(BURG,"BROADCAST BURGUNDY","#7A292A"),(WARM,"WARM NEUTRAL","#D6C8B5")]
for i,(col,nm,hexv) in enumerate(sw):
    x=50+i*145; rect(x,410,112,112,col); mono(x,378,nm,8); mono(x,360,hexv,9)
tx(50,275,"Usage hierarchy",16,True); mono(50,242,"01  Charcoal: wordmarks, type, frames, default UI.",10); mono(50,216,"02  Ivory: page field and long-read material.",10); mono(50,190,"03  Blue: information and current channel context.",10); mono(50,164,"04  Green: prepared/available workflow state. Burgundy: attention, exception, distinction.",10)
c.showPage()

title("05 / TYPOGRAPHY", "Technical without costume", "The production recommendation uses widely available system alternatives.",6)
tx(52,500,"ChannelDex",62,True); tx(55,440,"Arial / Helvetica",18,True); tx(55,411,"For interface labels, headings, and product wordmarks.",11)
mono(55,330,"PROGRAM INFORMATION  /  SCHEDULES  /  MEDIA ASSETS",15); tx(55,296,"Courier New / Courier",18,True); tx(55,267,"For metadata, record IDs, utility labels, and technical annotation.",11)
mono(520,510,"HIERARCHY",10); tx(520,475,"Headline",23,True); tx(520,439,"Section heading",15,True); tx(520,407,"Body text stays plain and direct.",11); mono(520,374,"RECORD CDX-2048 / 07:00",10)
c.showPage()

title("06 / VISUAL SYSTEM", "Information in a frame", "Use modular rectangles, fine rules, numbered records, and intentionally quiet empty space.",7)
for r in range(3):
 for q in range(4):
    x=55+q*165; y=395-r*75; rect(x,y,135,50,WHITE); c.setStrokeColor(WARM); c.rect(x,y,135,50,fill=0,stroke=1); mono(x+10,y+30,f"CH {q+1:02d} / {r+7:02d}:00",8); c.setStrokeColor(BLUE if q==0 else CHAR); c.line(x+10,y+15,x+125,y+15)
tx(55,150,"Favor documentation-grade graphics: records, schedule modules, labels, and status bands.",13,True)
mono(55,115,"Never turn these devices into decoration. Their job is to orient users and clarify state.",10)
c.showPage()

title("07 / APPLICATION & CORPORATE USE", "Small-scale identity", "The symbol is intentionally legible before the name becomes readable.",8)
image("channeldex-application-icon-color.png",55,145,265,265); image("capital-media-systems-corporate-mark.png",390,170,360,180)
mono(55,112,"APPLICATION ICON",10); mono(410,112,"CORPORATE MARK",10)
tx(55,75,"Use on browser surfaces and macOS packaging.",10); tx(410,75,"Use on parent collateral and future products.",10)
footer(8); c.save()

print(OUT)
