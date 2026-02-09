"""Temporal awareness utilities for memory system."""

import re
from datetime import datetime, timedelta
from typing import Optional, Tuple


def parse_temporal_reference(
    text: str,
    reference_date: Optional[datetime] = None
) -> Tuple[str, Optional[datetime]]:
    """
    Parse temporal references like 'tomorrow', 'next week' into absolute dates.
    
    Args:
        text: The text containing temporal references
        reference_date: The date to use as reference (default: now)
        
    Returns:
        Tuple of (enhanced_text, extracted_datetime)
    """
    if reference_date is None:
        reference_date = datetime.utcnow()
    
    enhanced_text = text
    extracted_datetime = None
    
    # Define patterns and their replacements
    patterns = [
        # Tomorrow
        (r'\btomorrow\b', 1, 'day'),
        # Today
        (r'\btoday\b', 0, 'day'),
        # Yesterday  
        (r'\byesterday\b', -1, 'day'),
        # Next week
        (r'\bnext week\b', 7, 'day'),
        # Next month
        (r'\bnext month\b', 1, 'month'),
        # In X days
        (r'\bin (\d+) days?\b', None, 'day'),
        # In X weeks
        (r'\bin (\d+) weeks?\b', None, 'week'),
        # In X months
        (r'\bin (\d+) months?\b', None, 'month'),
    ]
    
    for pattern, offset, unit in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            # Calculate actual date
            if offset is None:
                # Extract number from text
                offset = int(match.group(1))
            
            if unit == 'day':
                target_date = reference_date + timedelta(days=offset)
            elif unit == 'week':
                target_date = reference_date + timedelta(weeks=offset)
            elif unit == 'month':
                target_date = reference_date + timedelta(days=offset * 30)
            else:
                target_date = reference_date
            
            # Extract time if present (e.g., "at 3pm", "at 15:00")
            time_match = re.search(r'at (\d{1,2})(?::(\d{2}))?\s*(am|pm)?', text, re.IGNORECASE)
            if time_match:
                hour = int(time_match.group(1))
                minute = int(time_match.group(2)) if time_match.group(2) else 0
                meridiem = time_match.group(3)
                
                if meridiem:
                    if meridiem.lower() == 'pm' and hour < 12:
                        hour += 12
                    elif meridiem.lower() == 'am' and hour == 12:
                        hour = 0
                
                target_date = target_date.replace(hour=hour, minute=minute, second=0)
                extracted_datetime = target_date
            else:
                extracted_datetime = target_date
            
            # Enhance text with absolute date
            date_str = target_date.strftime("%B %d, %Y")
            time_str = ""
            
            if time_match:
                time_str = f" at {target_date.strftime('%I:%M %p')}"
            
            # Replace relative term with absolute date
            enhanced_text = re.sub(
                pattern,
                f"{match.group(0)} ({date_str}{time_str})",
                enhanced_text,
                flags=re.IGNORECASE
            )
            
            break  # Only process first match
    
    return enhanced_text, extracted_datetime


def format_relative_time(memory_content: str, memory_timestamp: datetime) -> str:
    """
    Convert stored temporal references to relative time from now.
    
    Args:
        memory_content: The memory content
        memory_timestamp: When the memory was created
        
    Returns:
        Enhanced content with updated relative times
    """
    now = datetime.utcnow()
    
    # Extract stored dates in format (Month Day, Year at HH:MM AM/PM)
    date_pattern = r'\(([A-Za-z]+) (\d+), (\d{4})(?: at (\d{1,2}):(\d{2}) (AM|PM))?\)'
    matches = re.finditer(date_pattern, memory_content)
    
    enhanced_content = memory_content
    
    for match in matches:
        month_name = match.group(1)
        day = int(match.group(2))
        year = int(match.group(3))
        
        # Parse the stored date
        month_map = {
            'January': 1, 'February': 2, 'March': 3, 'April': 4,
            'May': 5, 'June': 6, 'July': 7, 'August': 8,
            'September': 9, 'October': 10, 'November': 11, 'December': 12
        }
        month = month_map.get(month_name, 1)
        
        hour = int(match.group(4)) if match.group(4) else 0
        minute = int(match.group(5)) if match.group(5) else 0
        meridiem = match.group(6)
        
        if meridiem == 'PM' and hour < 12:
            hour += 12
        elif meridiem == 'AM' and hour == 12:
            hour = 0
        
        try:
            stored_date = datetime(year, month, day, hour, minute)
            
            # Calculate difference
            diff = (stored_date.date() - now.date()).days
            
            # Generate relative description
            if diff == 0:
                relative = "today"
            elif diff == 1:
                relative = "tomorrow"
            elif diff == -1:
                relative = "yesterday"
            elif diff > 1 and diff <= 7:
                relative = f"in {diff} days"
            elif diff < -1 and diff >= -7:
                relative = f"{abs(diff)} days ago"
            elif diff > 7:
                weeks = diff // 7
                relative = f"in {weeks} week{'s' if weeks > 1 else ''}"
            else:
                relative = f"{abs(diff // 7)} week{'s' if abs(diff // 7) > 1 else ''} ago"
            
            # Add time if available
            time_str = ""
            if match.group(4):
                time_str = f" at {stored_date.strftime('%I:%M %p')}"
            
            # Replace the parenthetical date with relative time
            enhanced_content = enhanced_content.replace(
                match.group(0),
                f"({relative}{time_str} - originally on {month_name} {day})"
            )
        except ValueError:
            # Invalid date, skip
            continue
    
    return enhanced_content


def extract_schedule_date(text: str) -> Optional[datetime]:
    """
    Extract a specific date/time from schedule-related text.
    
    Args:
        text: Text containing schedule information
        
    Returns:
        Extracted datetime or None
    """
    _, extracted_dt = parse_temporal_reference(text)
    return extracted_dt
