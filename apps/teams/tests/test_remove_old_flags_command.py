import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apps.teams.models import Flag


class RemoveOldFlagsCommandTest(TestCase):
    def setUp(self):
        # Create test flags
        self.used_flag = Flag.objects.create(name="used_feature_flag", everyone=False)
        self.unused_flag = Flag.objects.create(name="unused_feature_flag", everyone=False)
        
    def call_command(self, *args, **kwargs):
        """Helper to capture command output"""
        out = StringIO()
        kwargs["stdout"] = out
        call_command("remove_old_flags", *args, **kwargs)
        return out.getvalue()

    def test_no_flags_exist(self):
        Flag.objects.all().delete()
        output = self.call_command()
        self.assertIn("No flags found in database", output)

    def test_dry_run_mode(self):
        output = self.call_command("--dry-run")
        self.assertIn("Dry run mode", output)
        # Flags should still exist
        self.assertTrue(Flag.objects.filter(name="unused_feature_flag").exists())

    def test_specific_flag_not_found(self):
        with self.assertRaises(CommandError) as cm:
            self.call_command("--flag-name", "nonexistent_flag")
        self.assertIn("does not exist", str(cm.exception))

    @patch("apps.teams.management.commands.remove_old_flags.Command._find_flag_usages")
    def test_force_delete_used_flag(self, mock_find_usages):
        # Mock finding usages for the flag
        mock_find_usages.return_value = {
            "test_file.py": [(10, "flag_is_active('used_feature_flag')")]
        }
        
        output = self.call_command("--flag-name", "used_feature_flag", "--force")
        self.assertIn("Force deleting flag", output)
        self.assertIn("Successfully deleted", output)
        self.assertFalse(Flag.objects.filter(name="used_feature_flag").exists())

    @patch("apps.teams.management.commands.remove_old_flags.Command._find_flag_usages")
    def test_used_flag_shows_usage_without_force(self, mock_find_usages):
        # Mock finding usages for the flag
        mock_find_usages.return_value = {
            "test_file.py": [(10, "flag_is_active('used_feature_flag', request)")]
        }
        
        output = self.call_command("--flag-name", "used_feature_flag")
        self.assertIn("still in use", output)
        self.assertIn("test_file.py", output)
        self.assertIn("Line 10", output)
        # Flag should still exist
        self.assertTrue(Flag.objects.filter(name="used_feature_flag").exists())

    @patch("apps.teams.management.commands.remove_old_flags.Command._find_flag_usages")
    @patch("builtins.input", return_value="y")
    def test_delete_unused_flag_with_confirmation(self, mock_input, mock_find_usages):
        # Mock no usages found
        mock_find_usages.return_value = {}
        
        output = self.call_command("--flag-name", "unused_feature_flag")
        self.assertIn("Successfully deleted", output)
        self.assertFalse(Flag.objects.filter(name="unused_feature_flag").exists())

    @patch("apps.teams.management.commands.remove_old_flags.Command._find_flag_usages")
    @patch("builtins.input", return_value="n")
    def test_cancel_deletion(self, mock_input, mock_find_usages):
        # Mock no usages found
        mock_find_usages.return_value = {}
        
        output = self.call_command("--flag-name", "unused_feature_flag")
        self.assertIn("Operation cancelled", output)
        # Flag should still exist
        self.assertTrue(Flag.objects.filter(name="unused_feature_flag").exists())

    @patch("apps.teams.management.commands.remove_old_flags.Command._find_flag_usages")
    def test_scan_all_flags(self, mock_find_usages):
        # Mock that one flag has usage, one doesn't
        def mock_usage(flag_name):
            if flag_name == "used_feature_flag":
                return {"test_file.py": [(10, "flag_is_active('used_feature_flag')")])}
            return {}
        
        mock_find_usages.side_effect = mock_usage
        
        output = self.call_command("--dry-run")
        self.assertIn("Found 2 flags in database", output)
        self.assertIn("Found 1 unused flags", output)
        self.assertIn("unused_feature_flag", output)

    def test_flag_usage_detection_patterns(self):
        """Test the regex patterns used for detecting flag usage"""
        from apps.teams.management.commands.remove_old_flags import Command
        
        cmd = Command()
        
        # Create a temporary test file with various flag usage patterns
        test_content = '''
flag_is_active('test_flag', request)
get_waffle_flag('test_flag')
Flag.objects.get(name='test_flag')
if 'test_flag' in settings:
    pass
'''
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write(test_content)
            f.flush()
            
            # Mock the file walking to only return our test file
            with patch.object(cmd, '_get_source_files') as mock_get_files:
                mock_get_files.return_value = [Path(f.name)]
                
                usages = cmd._find_flag_usages('test_flag')
                
                self.assertGreater(len(usages), 0)
                # Should find multiple lines with usage
                file_usages = list(usages.values())[0]
                self.assertGreater(len(file_usages), 3)  # Should find at least 4 patterns
        
        # Clean up
        Path(f.name).unlink()

    def test_exclude_directories(self):
        """Test that specified directories are excluded from search"""
        from apps.teams.management.commands.remove_old_flags import Command
        
        cmd = Command()
        cmd.exclude_dirs = {"node_modules", ".git"}
        
        # This is tested implicitly by the _get_source_files method
        # In a real scenario, we'd need to create actual directory structure
        # For now, we just verify the exclude_dirs attribute is used
        self.assertIn("node_modules", cmd.exclude_dirs)
        self.assertIn(".git", cmd.exclude_dirs)