import openpyxl

wb = openpyxl.load_workbook('/home/ubuntu/活動報到系統/participants.xlsx')
ws = wb.active

print('Excel 目前資料（編號、姓名、公司、報到時間、狀態）：')
print('-' * 80)
for row in ws.iter_rows(min_row=2, values_only=True):
    if row[0]:
        checkin = str(row[7]) if row[7] else '（未報到）'
        status = str(row[8])
        print(f'{str(row[0]):<8} {str(row[1]):<10} {str(row[3]):<20} {checkin:<25} {status}')

wb.close()
