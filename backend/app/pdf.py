from io import BytesIO
from pathlib import Path
from datetime import datetime
from urllib.request import urlopen

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, KeepTogether
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

NAVY = colors.HexColor('#1E3A5F')
NAVY_DARK = colors.HexColor('#18345D')
TEAL = colors.HexColor('#0F766E')
GREEN = colors.HexColor('#10B981')
GREEN_DARK = colors.HexColor('#087443')
GREEN_SOFT = colors.HexColor('#E8F7F1')
RED = colors.HexColor('#DC4B4B')
RED_SOFT = colors.HexColor('#FDEEEE')
BLUE_SOFT = colors.HexColor('#EEF4FF')
GREY_SOFT = colors.HexColor('#F6F8FA')
GREY_MINT = colors.HexColor('#F4FAF8')
INK = colors.HexColor('#1F334D')
MUTED = colors.HexColor('#66788A')
LINE = colors.HexColor('#D7E5E1')
WHITE = colors.white

# DejaVu supports the Indian Rupee glyph reliably on Render/Linux.
try:
    pdfmetrics.registerFont(TTFont('BBDejaVu', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'))
    pdfmetrics.registerFont(TTFont('BBDejaVu-Bold', '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'))
except Exception:
    pass


def _money(value):
    return f'₹ {float(value or 0):,.2f}'


def _logo_flowable(logo_url, size=20):
    if not logo_url:
        return None
    try:
        data = urlopen(logo_url, timeout=4).read()
        img = Image(BytesIO(data), width=size * mm, height=size * mm)
        img.hAlign = 'CENTER'
        return img
    except Exception:
        return None


def _base_styles():
    s = getSampleStyleSheet()
    return {
        'title': ParagraphStyle('bbTitle', parent=s['Title'], fontName='BBDejaVu-Bold', fontSize=18, leading=21, textColor=NAVY_DARK, spaceAfter=1),
        'group': ParagraphStyle('bbGroup', parent=s['Heading2'], fontName='BBDejaVu-Bold', fontSize=16, leading=18, textColor=NAVY_DARK),
        'subtitle': ParagraphStyle('bbSub', parent=s['Normal'], fontName='BBDejaVu', fontSize=8.2, leading=10.5, textColor=MUTED),
        'section': ParagraphStyle('bbSection', parent=s['Heading2'], fontName='BBDejaVu-Bold', fontSize=11.5, leading=14, textColor=NAVY_DARK, spaceBefore=4, spaceAfter=5),
        'body': ParagraphStyle('bbBody', parent=s['Normal'], fontName='BBDejaVu', fontSize=8.2, leading=10.5, textColor=INK),
        'label': ParagraphStyle('bbLabel', parent=s['Normal'], fontName='BBDejaVu-Bold', fontSize=7.2, leading=9, textColor=MUTED),
        'metric': ParagraphStyle('bbMetric', parent=s['Normal'], fontName='BBDejaVu-Bold', fontSize=14, leading=16, textColor=INK),
        'metric_green': ParagraphStyle('bbMetricGreen', parent=s['Normal'], fontName='BBDejaVu-Bold', fontSize=14, leading=16, textColor=GREEN_DARK),
        'metric_red': ParagraphStyle('bbMetricRed', parent=s['Normal'], fontName='BBDejaVu-Bold', fontSize=14, leading=16, textColor=RED),
        'table_head': ParagraphStyle('bbTableHead', parent=s['Normal'], fontName='BBDejaVu-Bold', fontSize=7.0, leading=8.5, textColor=WHITE),
        'table': ParagraphStyle('bbTable', parent=s['Normal'], fontName='BBDejaVu', fontSize=7.0, leading=8.5, textColor=INK),
        'table_right': ParagraphStyle('bbTableRight', parent=s['Normal'], fontName='BBDejaVu', fontSize=7.0, leading=8.5, textColor=INK, alignment=TA_RIGHT),
        'credit': ParagraphStyle('bbCredit', parent=s['Normal'], fontName='BBDejaVu-Bold', fontSize=7.2, leading=8.5, textColor=GREEN_DARK, alignment=TA_RIGHT),
        'debit': ParagraphStyle('bbDebit', parent=s['Normal'], fontName='BBDejaVu-Bold', fontSize=7.2, leading=8.5, textColor=RED, alignment=TA_RIGHT),
        'footer': ParagraphStyle('bbFooter', parent=s['Normal'], fontName='BBDejaVu', fontSize=6.3, leading=8, textColor=MUTED, alignment=TA_CENTER),
        'small': ParagraphStyle('bbSmall', parent=s['Normal'], fontName='BBDejaVu', fontSize=6.8, leading=8.5, textColor=MUTED),
    }


def _doc(title):
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4, rightMargin=12*mm, leftMargin=12*mm,
        topMargin=10*mm, bottomMargin=16*mm, title=title,
        author='Bharat Bachat', subject='Bharat Bachat financial statement'
    )
    return buf, doc


def _footer(canvas, doc):
    canvas.saveState()
    w, _ = A4
    canvas.setStrokeColor(LINE)
    canvas.line(12*mm, 11*mm, w-12*mm, 11*mm)
    canvas.setFillColor(MUTED)
    canvas.setFont('Helvetica', 6.2)
    canvas.drawString(12*mm, 6.5*mm, 'System Generated Official Receipt  •  Bharat Bachat PWA')
    canvas.drawRightString(w-12*mm, 6.5*mm, f'Page {doc.page}')
    canvas.restoreState()




def _app_logo_flowable(size=13):
    path = Path(__file__).resolve().parent / 'assets' / 'bharat-bachat-logo.png'
    if path.exists():
        try:
            img = Image(str(path), width=size*mm, height=size*mm)
            img.hAlign = 'CENTER'
            return img
        except Exception:
            pass
    return _brand_mark(_base_styles())

def _brand_mark(styles):
    # Small vector-like Bharat Bachat mark made with table cells so the PDF does not depend on a frontend asset.
    mark = Table([[Paragraph('<font color="#FFFFFF"><b>INR</b></font>', ParagraphStyle('mark', parent=styles['body'], fontName='BBDejaVu-Bold', fontSize=7.5, leading=9, alignment=TA_CENTER))]], colWidths=[12*mm], rowHeights=[12*mm])
    mark.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),TEAL),('BOX',(0,0),(-1,-1),0.8,TEAL),('VALIGN',(0,0),(-1,-1),'MIDDLE'),('ALIGN',(0,0),(-1,-1),'CENTER')]))
    return mark


def _header(story, tenant_name, member_name=None, logo_url=None, title='SHG Passbook Statement'):
    st = _base_styles()
    group_logo = _logo_flowable(logo_url, 19)
    left_logo = group_logo or _brand_mark(st)
    group_text = [
        Paragraph(str(tenant_name or 'Bharat Bachat Group'), st['group']),
        Paragraph('Self Help Group (SHG)', st['subtitle']),
        Paragraph('Together We Grow  |  Financially Stronger', st['small']),
    ]
    left = Table([[left_logo, group_text]], colWidths=[22*mm, 73*mm])
    left.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'MIDDLE'),('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),3*mm),('TOPPADDING',(0,0),(-1,-1),0),('BOTTOMPADDING',(0,0),(-1,-1),0)]))

    now = datetime.now().strftime('%d %b %Y, %I:%M %p')
    right_brand = Table([
        [_app_logo_flowable(13), Paragraph('<b>Bharat Bachat</b>', st['title'])],
        ['', Paragraph('Aapki Bachat, Aapka Vikas', st['subtitle'])],
        ['', Paragraph(f'<b>Date of Export:</b>  {now}', st['small'])],
        ['', Paragraph(f'<b>Member Name:</b>  {member_name or "—"}', st['small'])],
    ], colWidths=[15*mm, 68*mm])
    right_brand.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'MIDDLE'),('ALIGN',(0,0),(-1,-1),'LEFT'),('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),0),('TOPPADDING',(0,0),(-1,-1),0.7*mm),('BOTTOMPADDING',(0,0),(-1,-1),0.7*mm)]))

    head = Table([[left, right_brand]], colWidths=[98*mm, 88*mm])
    head.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),0),('TOPPADDING',(0,0),(-1,-1),0),('BOTTOMPADDING',(0,0),(-1,-1),0)]))
    story.append(head)
    story.append(Spacer(1, 4*mm))
    story.append(Table([['', '']], colWidths=[78*mm, 108*mm], rowHeights=[1.2*mm], style=TableStyle([('BACKGROUND',(0,0),(0,0),GREEN),('BACKGROUND',(1,0),(1,0),NAVY_DARK)])))
    story.append(Spacer(1, 4*mm))
    story.append(Paragraph(title, st['title']))
    story.append(Paragraph('Account Summary & Transaction History', st['subtitle']))
    story.append(Spacer(1, 4*mm))


def _metric_card(label, value, style, bg):
    label_style = ParagraphStyle('metricLabel', parent=style, fontName='BBDejaVu-Bold', fontSize=7.3, leading=9, textColor=MUTED)
    cell = [Paragraph(label.upper(), label_style), Spacer(1, 1.5*mm), Paragraph(value, style)]
    return Table([[cell]], colWidths=[89*mm], rowHeights=[21*mm], style=TableStyle([
        ('BACKGROUND',(0,0),(-1,-1),bg),('BOX',(0,0),(-1,-1),0.7,LINE),('ROUNDEDCORNERS',[5,5,5,5]),
        ('VALIGN',(0,0),(-1,-1),'MIDDLE'),('LEFTPADDING',(0,0),(-1,-1),5*mm),('RIGHTPADDING',(0,0),(-1,-1),5*mm),('TOPPADDING',(0,0),(-1,-1),3*mm),('BOTTOMPADDING',(0,0),(-1,-1),3*mm)
    ]))


def _summary_grid(rows, st):
    cards=[]
    for label, value, kind in rows:
        style = st['metric_green'] if kind=='green' else st['metric_red'] if kind=='red' else st['metric']
        bg = GREEN_SOFT if kind=='green' else RED_SOFT if kind=='red' else colors.HexColor('#F7F9F8')
        cards.append(_metric_card(label, value, style, bg))
    t=Table([[cards[0],cards[1]],[cards[2],cards[3]]], colWidths=[91*mm,91*mm], hAlign='LEFT')
    t.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),0),('TOPPADDING',(0,0),(-1,-1),1.5*mm),('BOTTOMPADDING',(0,0),(-1,-1),1.5*mm)]))
    return t


def _transaction_table(rows, styles):
    data=[[Paragraph('DATE',styles['table_head']),Paragraph('TRANSACTION TYPE',styles['table_head']),Paragraph('ACCOUNT',styles['table_head']),Paragraph('CREDIT (₹)',styles['table_head']),Paragraph('DEBIT (₹)',styles['table_head']),Paragraph('BALANCE (₹)',styles['table_head'])]]
    for r in rows:
        amt=float(r.get('amount',0) or 0)
        bal=float(r.get('running_balance',0) or 0)
        typ=str(r.get('type','Transaction')).replace('_',' ').title()
        account=str(r.get('account','—')).title()
        credit=Paragraph(f'+{amt:,.2f}',styles['credit']) if amt>0 else Paragraph('—',styles['table_right'])
        debit=Paragraph(f'{abs(amt):,.2f}',styles['debit']) if amt<0 else Paragraph('—',styles['table_right'])
        data.append([Paragraph(str(r.get('date',''))[:10],styles['table']),Paragraph(typ[:28],styles['table']),Paragraph(account[:18],styles['table']),credit,debit,Paragraph(f'{bal:,.2f}',styles['table_right'])])
    t=Table(data,colWidths=[21*mm,46*mm,29*mm,27*mm,27*mm,36*mm],repeatRows=1,hAlign='LEFT')
    t.setStyle(TableStyle([
        ('BACKGROUND',(0,0),(-1,0),NAVY_DARK),('TEXTCOLOR',(0,0),(-1,0),WHITE),
        ('ROWBACKGROUNDS',(0,1),(-1,-1),[WHITE,colors.HexColor('#F4FAF8')]),
        ('GRID',(0,0),(-1,-1),0.35,LINE),('VALIGN',(0,0),(-1,-1),'MIDDLE'),
        ('LEFTPADDING',(0,0),(-1,-1),2.4*mm),('RIGHTPADDING',(0,0),(-1,-1),2.4*mm),
        ('TOPPADDING',(0,0),(-1,-1),2.7*mm),('BOTTOMPADDING',(0,0),(-1,-1),2.7*mm),
    ]))
    return t


def _notes_signature(tenant_name, styles):
    notes = Table([[Paragraph('<font color="#0F766E"><b>ⓘ  Note</b></font><br/><br/>• This passbook shows transactions recorded for your SHG account.<br/>• Closing balance reflects the running account balance represented by the selected passbook.<br/>• Please contact your SHG group leader for any discrepancy.', styles['body'])]], colWidths=[103*mm], rowHeights=[30*mm])
    notes.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),GREY_MINT),('BOX',(0,0),(-1,-1),0.6,LINE),('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),5*mm),('RIGHTPADDING',(0,0),(-1,-1),5*mm),('TOPPADDING',(0,0),(-1,-1),4*mm)]))
    sig = Table([[Paragraph('<b>SHG</b>', ParagraphStyle('seal', parent=styles['title'], fontSize=13, alignment=TA_CENTER, textColor=NAVY_DARK))],[Paragraph('Authorized Signatory', styles['small'])],[Paragraph('SHG President / Secretary', styles['body'])]], colWidths=[72*mm], rowHeights=[11*mm,8*mm,8*mm])
    sig.setStyle(TableStyle([('BOX',(0,0),(-1,-1),0.7,NAVY_DARK),('DASHED',(0,0),(-1,-1),1,3),('VALIGN',(0,0),(-1,-1),'MIDDLE'),('ALIGN',(0,0),(-1,-1),'CENTER'),('TOPPADDING',(0,0),(-1,-1),1*mm),('BOTTOMPADDING',(0,0),(-1,-1),1*mm)]))
    wrap=Table([[notes,sig]], colWidths=[108*mm,78*mm])
    wrap.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),0)]))
    return wrap


def passbook_pdf(tenant_name, member_name, rows, logo_url=None):
    buf, doc=_doc('Bharat Bachat Passbook Statement')
    st=_base_styles(); story=[]
    _header(story,tenant_name,member_name,logo_url,'SHG Passbook Statement')
    credits=sum(float(r.get('amount',0) or 0) for r in rows if float(r.get('amount',0) or 0)>0)
    debits=-sum(float(r.get('amount',0) or 0) for r in rows if float(r.get('amount',0) or 0)<0)
    closing=float(rows[-1].get('running_balance',0) or 0) if rows else 0
    story.append(_summary_grid([
        ('Total Entries',str(len(rows)),'neutral'),
        ('Total Credits',_money(credits),'green'),
        ('Total Debits',_money(debits),'red'),
        ('Closing Balance',_money(closing),'neutral'),
    ],st))
    story += [Spacer(1,5*mm), Paragraph('Detailed Passbook',st['section']), Spacer(1,1*mm), _transaction_table(rows,st), Spacer(1,6*mm), _notes_signature(tenant_name,st)]
    doc.build(story,onFirstPage=_footer,onLaterPages=_footer)
    buf.seek(0); return buf


def receipt_pdf(tenant_name, member_name, amount, receipt_type, reference, date_text='', account='', logo_url=None, note=''):
    buf, doc=_doc('Bharat Bachat Official Receipt')
    st=_base_styles(); story=[]
    _header(story,tenant_name,member_name,logo_url,'Official Payment Receipt')
    status='Recorded'
    info=Table([
        [Paragraph('<b>Receipt Type</b>',st['label']),Paragraph(str(receipt_type),st['body']),Paragraph('<b>Reference</b>',st['label']),Paragraph(str(reference),st['body'])],
        [Paragraph('<b>Member</b>',st['label']),Paragraph(str(member_name or '—'),st['body']),Paragraph('<b>Date</b>',st['label']),Paragraph(str(date_text or '—'),st['body'])],
        [Paragraph('<b>Account</b>',st['label']),Paragraph(str(account or '—').title(),st['body']),Paragraph('<b>Status</b>',st['label']),Paragraph(status,st['body'])],
    ],colWidths=[30*mm,61*mm,27*mm,68*mm])
    info.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),GREY_SOFT),('BOX',(0,0),(-1,-1),0.7,LINE),('INNERGRID',(0,0),(-1,-1),0.35,LINE),('VALIGN',(0,0),(-1,-1),'MIDDLE'),('LEFTPADDING',(0,0),(-1,-1),4*mm),('RIGHTPADDING',(0,0),(-1,-1),4*mm),('TOPPADDING',(0,0),(-1,-1),4*mm),('BOTTOMPADDING',(0,0),(-1,-1),4*mm)]))
    story.append(info); story.append(Spacer(1,7*mm))
    amount_box=Table([[Paragraph('TOTAL AMOUNT RECEIVED',st['label']),Paragraph(_money(amount),st['metric_green'])]],colWidths=[91*mm,95*mm],rowHeights=[25*mm])
    amount_box.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),GREEN_SOFT),('BOX',(0,0),(-1,-1),0.8,LINE),('VALIGN',(0,0),(-1,-1),'MIDDLE'),('LEFTPADDING',(0,0),(-1,-1),6*mm),('RIGHTPADDING',(0,0),(-1,-1),6*mm)]))
    story.append(amount_box)
    if note:
        story += [Spacer(1,6*mm), Paragraph('Transaction Note',st['section']), Paragraph(str(note),st['body'])]
    story += [Spacer(1,35*mm), _notes_signature(tenant_name,st)]
    doc.build(story,onFirstPage=_footer,onLaterPages=_footer)
    buf.seek(0); return buf


def receipt_bundle_pdf(tenant_name, member_name, rows, logo_url=None):
    # The receipt bundle intentionally uses the same passbook visual system so every PDF from the app feels like one product.
    return passbook_pdf(tenant_name, member_name, rows, logo_url)
