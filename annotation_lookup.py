#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
工具函数模块
"""

def get_student_annotation_by_char_id(student_annotations, char_id):
    """
    从学生标注数据列表中获取特定字符的标注数据
    
    Args:
        student_annotations (list): 学生标注数据列表
        char_id (str): 字符ID
    
    Returns:
        dict: 特定字符的标注数据，如果未找到则返回空字典
    """
    return next((ann for ann in student_annotations if ann.get('char_id') == char_id), {})

def get_all_student_annotations_dict(student_annotations):
    """
    将学生标注数据列表转换为字典格式，便于快速查找
    
    Args:
        student_annotations (list): 学生标注数据列表
    
    Returns:
        dict: 以char_id为键的字典格式数据
    """
    return {ann['char_id']: ann for ann in student_annotations} if student_annotations else {}