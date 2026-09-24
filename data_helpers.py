#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
數據處理輔助函數
處理排序、分頁、學生數據合併等邏輯
"""

import logging

logger = logging.getLogger(__name__)

def extract_code_from_other_info(other_info):
    """
    從 other_info 中提取寫卷編碼，用於分組。
    
    改進規則（按「年代_寫卷編號」分組）：
    - 取末段（最後一個下劃線之後）的前 3 個字符作為寫卷編號，其餘視為頁碼。
    - 分組鍵固定為：`DH_<年代>_<寫卷前三位>`，可包含字母（如 020Y）。
    """
    if not other_info:
        return ''

    if not other_info.startswith('DH_'):
        return other_info

    parts = other_info.split('_')
    if len(parts) < 3:
        return other_info

    head = '_'.join(parts[:-1])
    tail = parts[-1]
    base_tail = tail[:3] if len(tail) > 3 else tail
    return f"{head}_{base_tail}"

def extract_page_from_other_info(other_info):
    """從other_info中提取頁碼部分"""
    if not other_info or not other_info.startswith('DH_'):
        return 0
    try:
        # 假設最後兩位是頁碼
        return int(other_info[-2:])
    except (ValueError, IndexError):
        try:
            # 如果失敗，嘗試最後一位
            return int(other_info[-1:])
        except (ValueError, IndexError):
            return 0

def merge_student_marks(data_list, student_marks_dict, all_marks_dict=None, current_student_id=None):
    """
    將學生標記數據合併到字符數據中

    Args:
        data_list: 字符數據列表
        student_marks_dict: 當前學生標記數據字典 {char_id: {Situation, where, operation, 備註}}
        all_marks_dict: 所有學生標記 {char_id: {student_id: {...}}}（可選，用於顯示前人成果）
        current_student_id: 當前學生ID（可選）

    Returns:
        list: 合併後的數據列表
    """
    for row in data_list:
        char_id = row.get('char_id', '')
        if char_id in student_marks_dict:
            student_data = student_marks_dict[char_id]
            row['Situation'] = student_data.get('Situation', '')
            row['where'] = student_data.get('where', '')
            row['operation'] = student_data.get('operation', '')
            row['備註'] = student_data.get('備註', '')
        else:
            row['Situation'] = ''
            row['where'] = ''
            row['operation'] = ''
            row['備註'] = ''

        # 收集其他學生的標注（前人成果）
        other_marks = []
        if all_marks_dict and char_id in all_marks_dict:
            for sid, marks in all_marks_dict[char_id].items():
                if sid == current_student_id:
                    continue  # 跳過自己的（已經在上面處理了）
                # 只收集有實際內容的標注
                has_content = any(marks.get(k) for k in ('Situation', 'where', 'operation', '備註'))
                if has_content:
                    other_marks.append({
                        'student_id': sid,
                        'Situation': marks.get('Situation', ''),
                        'where': marks.get('where', ''),
                        'operation': marks.get('operation', ''),
                        '備註': marks.get('備註', ''),
                        'created_at': marks.get('created_at', ''),
                        'updated_at': marks.get('updated_at', '')
                    })
        row['other_marks'] = other_marks

        # 添加編碼信息用於背景色計算
        other_info = row.get('other_info', '')
        row['code_for_color'] = extract_code_from_other_info(other_info)

    return data_list

def build_sort_sql(sort_column, sort_direction):
    """
    構建 SQL ORDER BY 子句
    
    Args:
        sort_column: 排序列名
        sort_direction: 'asc' 或 'desc'
    
    Returns:
        str: ORDER BY 子句（不包含 ORDER BY 關鍵字）
    """
    if not sort_column or not sort_direction:
        return None
    
    # 對於 other_info 的特殊排序，我們在 Python 層面處理
    # 因為 SQL 無法實現複雜的自定義排序邏輯
    if sort_column == 'other_info':
        return None  # 返回 None 表示需要在 Python 層面排序
    
    return f"{sort_column} {sort_direction.upper()}"

def sort_data_in_python(data, sort_column=None, sort_direction=None):
    """
    在 Python 層面對數據進行排序（用於複雜排序邏輯）
    
    Args:
        data: 數據列表
        sort_column: 排序列
        sort_direction: 'asc' 或 'desc'
    
    Returns:
        list: 排序後的數據
    """
    if not sort_column or not sort_direction:
        return data

    reverse = sort_direction == 'desc'

    # 針對「年代信息」的特殊排序邏輯
    if sort_column == 'other_info':
        def get_custom_sort_key(item):
            other_info = item.get('other_info', '')
            code = extract_code_from_other_info(other_info)
            page = extract_page_from_other_info(other_info)

            # 根據標記分配排序權重
            situation = item.get('Situation', '')
            where = str(item.get('where', ''))
            operation = item.get('operation', '')

            status_weight = 0
            if situation == 'Bad':
                status_weight = 3  # 殘損
            elif where.startswith('Mis/'):
                status_weight = 2  # 誤入
            elif operation and operation.strip() and operation.split('/')[-1] in ['1', '2', '3', '4']:
                status_weight = 1  # 數字標記

            # 注意標記優先級排序
            h_atten = item.get('h_atten', '')
            r_atten = item.get('r_atten', '')
            attention_priority = 0
            
            if h_atten == 'need' and r_atten == 'need':
                attention_priority = 3  # 同時標註人工和機器，最高優先級
            elif h_atten == 'need':
                attention_priority = 2  # 只標註人工，第二優先級
            elif r_atten == 'need':
                attention_priority = 1  # 只標註機器，第三優先級
            else:
                attention_priority = 0  # 沒有標註，最低優先級

            # 注意：attention_priority 需要反向排序，因為我們希望高優先級的排在前面
            # 使用負數來實現反向排序
            return (code, -attention_priority, status_weight, page)

        sorted_data = sorted(data, key=get_custom_sort_key, reverse=reverse)
        return sorted_data

    # 對於其他列，使用原始的排序邏輯
    numeric_columns = {'char_id', 'column_no', 'char_no', 'cid', 'x', 'y', 'w', 'h'}

    def get_sort_key(item):
        value = item.get(sort_column, '')
        if sort_column in numeric_columns:
            try:
                return int(value) if value else 0
            except ValueError:
                return 0
        return str(value)  # 確保所有非數字比較都是字符串比較

    sorted_data = sorted(data, key=get_sort_key, reverse=reverse)
    return sorted_data
