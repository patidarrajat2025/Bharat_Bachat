from io import BytesIO
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether, Image
from urllib.request import urlopen

NAVY = colors.HexColor('#1A2B4C')
GREEN = colors.HexColor('#0F9D58')
GREEN_DARK = colors.HexColor('#087443')
GREEN_SOFT = colors.HexColor('#E6F4EA')
RED = colors.HexColor('#C5221F')
RED_SOFT = colors.HexColor('#FCE8E6')
INK = colors.HexColor('#23352C')
MUTED = colors.HexColor('#718078')
LINE = colors.HexColor('#D9E6DE')
WHITE = colors.white


def _logo_flowable(logo_url):
    if not logo_url:
        return None
    try:
        data = urlopen(logo_url, timeout=4).read()
        img = Image(BytesIO(data), width=15*mm, height=15*mm)
        img.hAlign = 'LEFT'
        return img
    except Exception:
        return None


def _base_styles():
    styles = getSampleStyleSheet()
    return {
        'title': ParagraphStyle('bbTitle', parent=styles['Title'], fontName='Helvetica-Bold', fontSize=20, leading=23, textColor=NAVY, spaceAfter=2),
        'subtitle': ParagraphStyle('bbSub', parent=styles['Normal'], fontName='Helvetica', fontSize=8.5, leading=11, textColor=MUTED),
        'section': ParagraphStyle('bbSection', parent=styles['Heading2'], fontName='Helvetica-Bold', fontSize=11.5, leading=14, textColor=NAVY, spaceBefore=5, spaceAfter=6),
        'body': ParagraphStyle('bbBody', parent=styles['Normal'], fontName='Helvetica', fontSize=8.5, leading=11, textColor=INK),
        'label': ParagraphStyle('bbLabel', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=7.5, leading=9, textColor=MUTED),
        'value': ParagraphStyle('bbValue', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=9, leading=11, textColor=INK),
        'amount': ParagraphStyle('bbAmount', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=11, leading=13, textColor=GREEN_DARK, alignment=TA_RIGHT),
        'amount_red': ParagraphStyle('bbAmountRed', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=11, leading=13, textColor=RED, alignment=TA_RIGHT),
        'table_head': ParagraphStyle('bbTableHead', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=7.2, leading=9, textColor=WHITE),
        'table': ParagraphStyle('bbTable', parent=styles['Normal'], fontName='Helvetica', fontSize=7.2, leading=9, textColor=INK),
        'table_right': ParagraphStyle('bbTableRight', parent=styles['Normal'], fontName='Helvetica', fontSize=7.2, leading=9, textColor=INK, alignment=TA_RIGHT),
        'footer': ParagraphStyle('bbFooter', parent=styles['Normal'], fontName='Helvetica', fontSize=6.5, leading=8, textColor=MUTED, alignment=TA_CENTER),
    }


def _header(story, tenant_name, member_name=None, logo_url=None, title='Bharat Bachat'):
    st = _base_styles()
    logo = _logo_flowable(logo_url)
    brand = [[Paragraph(f'<b>{title}</b>', st['title']), Paragraph('Aapki Bachat, Aapka Vikas', st['subtitle'])]]
    if logo:
        brand = [[logo, brand[0][0], brand[0][1]]]
        table = Table(brand, colWidths=[18*mm, 70*mm, 75*mm], rowHeights=[18*mm])
        table.setStyle(TableStyle([
            ('VALIGN',(0,0),(-1,-1),'MIDDLE'),
            ('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),4),
        ]))
    else:
        table = Table([[brand[0][0], brand[0][1]]], colWidths=[95*mm, 70*mm])
        table.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'MIDDLE'),('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),0)]))
    story += [table, Spacer(1, 4*mm)]
    meta = [[Paragraph(str(tenant_name or 'Bharat Bachat Group'), st['section']), Paragraph(f'Member: {member_name}' if member_name else '', st['subtitle'])]]
    meta_table = Table(meta, colWidths=[100*mm, 65*mm])
    meta_table.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'MIDDLE'),('ALIGN',(1,0),(1,0),'RIGHT'),('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),0),('BOTTOMPADDING',(0,0),(-1,-1),3*mm)]))
    story.append(meta_table)


def _footer(canvas, doc):
    canvas.saveState()
    w, _ = A4
    canvas.setStrokeColor(LINE); canvas.line(18*mm, 13*mm, w-18*mm, 13*mm)
    canvas.setFillColor(MUTED); canvas.setFont('Helvetica', 6.5)
    canvas.drawString(18*mm, 8.5*mm, 'System generated • Bharat Bachat')
    canvas.drawRightString(w-18*mm, 8.5*mm, f'Page {doc.page}')
    canvas.restoreState()


def _doc(title):
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, rightMargin=18*mm, leftMargin=18*mm, topMargin=15*mm, bottomMargin=17*mm, title=title, author='Bharat Bachat')
    return buf, doc


def _info_grid(rows, styles):
    data=[]
    for i in range(0, len(rows), 2):
        pair=rows[i:i+2]
        row=[]
        for label,value in pair:
            row.append(Paragraph(f'<font color="#718078" size="7"><b>{label}</b></font><br/><font color="#23352C" size="9"><b>{value}</b></font>', styles['body']))
        while len(row)<2: row.append('')
        data.append(row)
    t=Table(data,colWidths=[82.5*mm,82.5*mm],hAlign='LEFT')
    t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),colors.HexColor('#F8FBF9')),('BOX',(0,0),(-1,-1),0.6,LINE),('INNERGRID',(0,0),(-1,-1),0.5,LINE),('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),5*mm),('RIGHTPADDING',(0,0),(-1,-1),5*mm),('TOPPADDING',(0,0),(-1,-1),4*mm),('BOTTOMPADDING',(0,0),(-1,-1),4*mm)]))
    return t


def receipt_pdf(tenant_name, member_name, amount, receipt_type, reference, date_text='', account='', logo_url=None, note=''):
    buf, doc = _doc('Bharat Bachat Receipt')
    st=_base_styles(); story=[]
    _header(story,tenant_name,member_name,logo_url,'Bharat Bachat')
    story.append(Table([[Paragraph('<b>PAYMENT RECEIPT</b>', st['table_head'])]], colWidths=[165*mm], style=TableStyle([('BACKGROUND',(0,0),(-1,-1),GREEN),('LEFTPADDING',(0,0),(-1,-1),5*mm),('TOPPADDING',(0,0),(-1,-1),3*mm),('BOTTOMPADDING',(0,0),(-1,-1),3*mm)])))
    story.append(Spacer(1,4*mm))
    story.append(_info_grid([
        ('Receipt Type', receipt_type),('Reference', reference),('Member', member_name),('Date', date_text),('Account', account or '—'),('Status','Recorded'),
    ],st))
    story.append(Spacer(1,5*mm))
    amount_table=Table([[Paragraph('TOTAL AMOUNT',st['label']),Paragraph(f'₹ {float(amount or 0):,.2f}',st['amount'])]],colWidths=[82.5*mm,82.5*mm])
    amount_table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),GREEN_SOFT),('BOX',(0,0),(-1,-1),0.8,LINE),('LEFTPADDING',(0,0),(-1,-1),5*mm),('RIGHTPADDING',(0,0),(-1,-1),5*mm),('TOPPADDING',(0,0),(-1,-1),5*mm),('BOTTOMPADDING',(0,0),(-1,-1),5*mm),('VALIGN',(0,0),(-1,-1),'MIDDLE')]))
    story.append(amount_table)
    if note:
        story += [Spacer(1,5*mm),Paragraph('Note',st['section']),Paragraph(str(note),st['body'])]
    story += [Spacer(1,10*mm),Paragraph('Please retain this receipt as proof of the recorded transaction.',st['footer'])]
    doc.build(story,onFirstPage=_footer,onLaterPages=_footer)
    buf.seek(0); return buf


def _transaction_table(rows, styles):
    data=[[Paragraph('DATE',styles['table_head']),Paragraph('ENTRY',styles['table_head']),Paragraph('ACCOUNT',styles['table_head']),Paragraph('CREDIT',styles['table_head']),Paragraph('DEBIT',styles['table_head']),Paragraph('BALANCE',styles['table_head'])]]
    for r in rows:
        amt=float(r.get('amount',0) or 0); bal=float(r.get('running_balance',0) or 0)
        data.append([Paragraph(str(r.get('date',''))[:10],styles['table']),Paragraph(str(r.get('type','Transaction')).replace('_',' ').title()[:28],styles['table']),Paragraph(str(r.get('account','—'))[:12],styles['table']),Paragraph(f'{amt:,.2f}' if amt>=0 else '—',styles['table_right']),Paragraph(f'{abs(amt):,.2f}' if amt<0 else '—',styles['table_right']),Paragraph(f'{bal:,.2f}',styles['table_right'])])
    t=Table(data,colWidths=[20*mm,47*mm,24*mm,24*mm,24*mm,26*mm],repeatRows=1,hAlign='LEFT')
    t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),NAVY),('ROWBACKGROUNDS',(0,1),(-1,-1),[WHITE,colors.HexColor('#F7FBF9')]),('GRID',(0,0),(-1,-1),0.35,LINE),('VALIGN',(0,0),(-1,-1),'MIDDLE'),('LEFTPADDING',(0,0),(-1,-1),2.5*mm),('RIGHTPADDING',(0,0),(-1,-1),2.5*mm),('TOPPADDING',(0,0),(-1,-1),2.4*mm),('BOTTOMPADDING',(0,0),(-1,-1),2.4*mm)]))
    return t


def passbook_pdf(tenant_name, member_name, rows, logo_url=None):
    buf, doc=_doc('Bharat Bachat Passbook'); st=_base_styles(); story=[]
    _header(story,tenant_name,member_name,logo_url,'Bharat Bachat Passbook')
    credit=sum(float(r.get('amount',0) or 0) for r in rows if float(r.get('amount',0) or 0)>=0); debit=-sum(float(r.get('amount',0) or 0) for r in rows if float(r.get('amount',0) or 0)<0)
    summary=Table([[Paragraph(f'<b>Transactions</b><br/><font size="13">{len(rows)}</font>',st['body']),Paragraph(f'<b>Credits</b><br/><font color="#087443" size="13">₹ {credit:,.2f}</font>',st['body']),Paragraph(f'<b>Debits</b><br/><font color="#C5221F" size="13">₹ {debit:,.2f}</font>',st['body'])]],colWidths=[55*mm]*3)
    summary.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),colors.HexColor('#F8FBF9')),('BOX',(0,0),(-1,-1),0.6,LINE),('INNERGRID',(0,0),(-1,-1),0.5,LINE),('LEFTPADDING',(0,0),(-1,-1),4*mm),('TOPPADDING',(0,0),(-1,-1),4*mm),('BOTTOMPADDING',(0,0),(-1,-1),4*mm)]))
    story += [summary,Spacer(1,6*mm),Paragraph('Transaction History',st['section']),_transaction_table(rows,st)]
    doc.build(story,onFirstPage=_footer,onLaterPages=_footer); buf.seek(0); return buf


def receipt_bundle_pdf(tenant_name, member_name, rows, logo_url=None):
    buf, doc=_doc('Bharat Bachat Receipt & Passbook'); st=_base_styles(); story=[]
    _header(story,tenant_name,member_name,logo_url,'Bharat Bachat Receipt')
    story.append(Paragraph('TRANSACTION SUMMARY',st['section']))
    credit=sum(float(r.get('amount',0) or 0) for r in rows if float(r.get('amount',0) or 0)>=0); debit=-sum(float(r.get('amount',0) or 0) for r in rows if float(r.get('amount',0) or 0)<0)
    story.append(_info_grid([('Entries',len(rows)),('Credits',f'₹ {credit:,.2f}'),('Debits',f'₹ {debit:,.2f}'),('Closing Balance',f'₹ {float(rows[-1].get("running_balance",0) or 0):,.2f}' if rows else '₹ 0.00')],st))
    story += [Spacer(1,6*mm),Paragraph('Detailed Passbook',st['section']),_transaction_table(rows,st)]
    doc.build(story,onFirstPage=_footer,onLaterPages=_footer); buf.seek(0); return buf
