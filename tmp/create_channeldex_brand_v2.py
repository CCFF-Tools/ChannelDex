from pathlib import Path
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader

out=Path.cwd()/"output/channeldex-brand-v2"
pdf=out/"ChannelDex_Brand_Guide_v2.pdf"
W,H=letter; W,H=H,W
c=canvas.Canvas(str(pdf),pagesize=(W,H))
ivory="#EEE7D9"; charcoal="#181A1B"; blue="#1E5A9E"; green="#426F4B"; burgundy="#842A2A"; taupe="#CFC2AD"
def bg(): c.setFillColor(ivory); c.rect(0,0,W,H,fill=1,stroke=0)
def t(x,y,s,n=12,b=False,col=charcoal): c.setFillColor(col); c.setFont("Helvetica-Bold" if b else "Helvetica",n); c.drawString(x,y,s)
def m(x,y,s,n=9): c.setFillColor(charcoal); c.setFont("Courier",n); c.drawString(x,y,s)
def rule(y): c.setStrokeColor(taupe); c.setLineWidth(.8); c.line(52,y,W-52,y)
def foot(n): rule(36); m(52,19,"CHANNELDEX  /  PROPOSED BRAND SYSTEM",8); m(W-68,19,f"0{n}",8)
def im(n,x,y,w,h): c.drawImage(ImageReader(str(out/n)),x,y,w,h,preserveAspectRatio=True,anchor='c',mask='auto')
def head(k,h,sub,n):
 bg(); m(52,H-48,k,9); t(52,H-110,h,30,True); t(52,H-140,sub,12); foot(n)

head("CHANNELDEX / IDENTITY GUIDE", "Organized broadcast information.", "A proposed identity for an enduring television programming system.",1)
im("channeldex-by-capital-media-systems-color.png",60,270,670,190)
t(60,190,"Institutional computing restraint, adapted for a modern browser application.",15,True)
m(60,153,"CHANNEL  /  CATALOG  /  SCHEDULE  /  MEDIA  /  WORKFLOW",10)
c.showPage()

head("01 / PRIMARY IDENTITY", "The catalog frame.", "A compact symbol that reads as screen, indexed record, and channel slot.",2)
im("channeldex-color.png",60,362,672,154)
im("channeldex-monochrome.png",60,138,672,154)
m(60,330,"COLOR MASTER  -  CHARCOAL WORDMARK / SIGNAL ACCENTS",9)
m(60,108,"ONE-INK VERSION  -  USE FOR STAMPS, LABELS, AND LOW-INK OUTPUT",9)
c.showPage()

head("02 / ENDORSEMENT", "ChannelDex leads.", "Capital Media Systems provides a quiet, credible parent signature.",3)
im("channeldex-by-capital-media-systems-color.png",60,365,672,160)
im("channeldex-by-capital-media-systems-monochrome.png",60,160,672,160)
t(60,108,"Use the endorsed lockup for manuals, login views, product pages, and launch material.",11)
c.showPage()

head("03 / COLOR", "Broadcast, not neon.", "Charcoal and warm ivory do the structural work; accents communicate purpose.",4)
palette=[(charcoal,"CHARCOAL","#181A1B"),(blue,"STATION BLUE","#1E5A9E"),(green,"MONITOR GREEN","#426F4B"),(burgundy,"BROADCAST RED","#842A2A"),(taupe,"WARM NEUTRAL","#CFC2AD")]
for i,(col,name,hx) in enumerate(palette):
 x=55+i*138; c.setFillColor(col); c.rect(x,395,104,104,fill=1,stroke=0); m(x,365,name,7); m(x,348,hx,8)
t(55,270,"Use color as a signal, not decoration.",16,True)
m(55,235,"CHARCOAL  /  DEFAULT TYPE, FRAMES, NAVIGATION",9)
m(55,209,"BLUE  /  CHANNEL CONTEXT AND INFORMATION",9)
m(55,183,"GREEN  /  AVAILABLE, PREPARED, OR CONFIRMED WORKFLOW STATE",9)
m(55,157,"BURGUNDY  /  EMPHASIS, EXCEPTIONS, AND PROGRAM DISTINCTIVENESS",9)
c.showPage()

head("04 / TYPE & LAYOUT", "Technical without costume.", "Favor robust corporate sans serif with a restrained monospaced utility voice.",5)
t(58,380,"ChannelDex",58,True); t(60,340,"Helvetica / Arial: headings, navigation, and prose.",13)
m(60,260,"PROGRAM INFORMATION  /  SCHEDULES  /  MEDIA ASSETS",16)
t(60,220,"Courier: record IDs, labels, timestamps, and technical annotation.",13)
rule(165); m(60,132,"CDX-2048   |   CITY-TV   |   19:00   |   PREPARED",11)
t(60,82,"Build pages from measured columns, thin rules, and generous empty space.",13,True)
c.showPage()

head("05 / SMALL-SCALE MARKS", "Recognizable before readable.", "The catalog-frame symbol carries the product at app and browser scale.",6)
im("channeldex-application-icon-color.png",74,175,275,275)
im("channeldex-favicon-color.png",480,245,175,175)
m(74,140,"APPLICATION ICON",9); m(480,215,"FAVICON",9)
t(74,105,"Use the full icon for desktop and login surfaces.",10)
t(480,180,"Use the reduced mark at 16-32 px.",10)
c.showPage()

head("06 / CORPORATE MARK", "A parent with a point of view.", "The Capitol-and-signal geometry belongs to Capital Media Systems, not ChannelDex.",7)
im("capital-media-systems-corporate-mark.png",135,210,520,250)
t(120,150,"Use at parent-organization scale, supporting a product family without competing with it.",11)
m(120,116,"CAPITAL MEDIA SYSTEMS  /  PROGRAMMING A BRIGHTER TOMORROW",9)
c.save()
print(pdf)
